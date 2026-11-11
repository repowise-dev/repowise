"""Gather a change's evidence from git and the index, and keep it on its row.

:func:`evidence_for_revspec` reviews any commit, range or the working tree;
:func:`build_stored_evidence` does it for a stored Change Record without
writing, :func:`save_evidence` keeps the result on ``changes.evidence_json``,
and :func:`change_evidence` does both. All end in
:func:`~repowise.core.changes.evidence.build_change_evidence`.

A review costs seconds per change (the health comparison parses both sides),
so it is computed on first read, never during an index or update, and kept:
a merged change's checklist is a snapshot stamped with ``generated_at`` and
the ``index_commit`` it read, rebuilt only on request (``refresh``). The
``outcomes`` item is the exception: it describes history after the change, so
a kept checklist has that one item rebuilt when HEAD moves
(:func:`with_current_outcomes`), which costs one history read per HEAD.
Components and owners are resolved again on read when the component catalog
changed since the checklist was kept (:func:`with_current_components`).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import structlog

from repowise.core.changes.evidence import (
    EVIDENCE_SCHEMA_VERSION,
    KINDS,
    SUMMARY_SKIPPED_KINDS,
    ChangeEvidence,
    EvidenceInputs,
    EvidenceItem,
    Unavailable,
    build_change_evidence,
)
from repowise.core.components.catalog import catalog_digest
from repowise.core.ownership.codeowners import (
    CodeownersMatcher,
    parse_codeowners,
    read_codeowners,
)

if TYPE_CHECKING:
    from repowise.core.persistence.crud import ChangeFilter

logger = structlog.get_logger(__name__)

#: Reviewers listed per change; more is no better than none.
REVIEWER_LIMIT = 5

NO_INDEX = "the repository has no index"

#: Decision statuses that no longer bind anything.
_RETIRED = frozenset({"dismissed", "superseded", "deprecated"})

#: The basis kind behind a reviewer, never a weight or a count.
_REVIEWER_REASONS = {"touched": "owns path", "co-change history": "co-change"}

_CO_AUTHOR = re.compile(r"^\s*(.*?)\s*<([^>]*)>\s*$")


# -- review -------------------------------------------------------------------


def _review(
    repo_root: str,
    revspec: str | None,
    *,
    tests: dict[str, Any] | None,
    risk: Any = None,
    health: Any = None,
    extensions: tuple[str, ...] = (),
    exclude_patterns: tuple[str, ...] = (),
) -> Any:
    from repowise.core.analysis.change_health.sources import GitRevisionSource
    from repowise.core.analysis.change_review import (
        ChangeReviewEvidence,
        ChangeReviewRequest,
        ChangeReviewService,
    )

    service = ChangeReviewService(GitRevisionSource(repo_root), repo_path=repo_root)
    return service.review(
        ChangeReviewRequest(
            revspec=revspec, extensions=extensions, exclude_patterns=exclude_patterns
        ),
        evidence=ChangeReviewEvidence(risk=risk, health=health, tests=tests),
    )


def _manifest(
    repo_root: str,
    revspec: str | None,
    extensions: tuple[str, ...],
    exclude_patterns: tuple[str, ...],
) -> dict[str, str]:
    """``{path: status}`` the review will count, read before it runs."""
    from repowise.core.analysis.change_health.sources import GitRevisionSource, filter_changes

    pair = GitRevisionSource(repo_root, hunks=False).resolve(revspec)
    changes = filter_changes(
        pair.changes, extensions=extensions, exclude_patterns=exclude_patterns
    )
    return {c.path: c.status for c in changes}


async def evidence_for_revspec(
    session: Any | None,
    repo_id: str,
    repo_root: Path | str,
    revspec: str | None,
    *,
    change_id: str | None = None,
    row: Any = None,
    risk: Any = None,
    health: Any = None,
    extensions: Sequence[str] = (),
    exclude_patterns: Sequence[str] = (),
    no_index: str = NO_INDEX,
) -> ChangeEvidence:
    """The checklist for *revspec* (``None`` is the working tree).

    *row* is the stored change *revspec* names, when there is one: its id,
    commits and stored provenance are used. *risk* and *health* are results
    the caller already computed; omitted, the review computes them. Without a
    *session* only the git-derived items are filled, and *no_index* says why.
    """
    from repowise.core.analysis.test_impact import analyze_test_impact
    from repowise.core.persistence.crud import get_repository

    root = str(repo_root)
    ext, excl = tuple(extensions), tuple(exclude_patterns)
    try:
        statuses = await asyncio.to_thread(_manifest, root, revspec, ext, excl)
    except (ValueError, OSError) as exc:
        statuses, missing = {}, str(exc)
    else:
        missing = None if session is not None else no_index
    tests = None
    if statuses and session is not None:
        try:
            tests = await analyze_test_impact(
                session, repo_id, sorted(statuses), change_status=statuses
            )
        except Exception as exc:
            logger.debug("evidence_tests_failed", error=str(exc))
    bundle = await asyncio.to_thread(
        _review,
        root,
        revspec,
        tests=tests,
        risk=risk,
        health=health,
        extensions=ext,
        exclude_patterns=excl,
    )
    if row is not None:
        change_id = row.change_id
    if missing:
        gone = Unavailable(missing)
        inputs = EvidenceInputs(
            components=gone,
            owners=gone,
            reviewers=gone,
            contracts=gone,
            consumers=gone,
            decisions=gone,
            provenance=gone,
        )
        index_commit = digest = None
    else:
        shas = await asyncio.to_thread(_change_commits, root, revspec, row)
        digest = await asyncio.to_thread(catalog_digest, root)
        inputs = await gather_inputs(
            session,
            repo_id,
            Path(root),
            [e.path for e in bundle.manifest],
            shas=shas,
            row=row,
        )
        index_commit = getattr(await get_repository(session, repo_id), "head_commit", None)
    evidence = build_change_evidence(
        bundle, inputs, change_id=change_id, revspec=revspec, index_commit=index_commit
    )
    if isinstance(inputs.components, Unavailable):
        digest = None  # not resolved: the next read resolves again
    return replace(evidence, catalog_digest=digest)


# -- stored changes -------------------------------------------------------------


async def build_stored_evidence(
    session: Any,
    repo_id: str,
    repo_root: Path | str,
    row: Any,
    *,
    risk: Any = None,
    health: Any = None,
) -> ChangeEvidence:
    """The stored change's checklist, built now and not written.

    The change is its head commit against that commit's first parent.
    """
    return await evidence_for_revspec(
        session, repo_id, repo_root, row.head_sha, row=row, risk=risk, health=health
    )


async def save_evidence(
    session: Any, repo_id: str, change_id: str, evidence: ChangeEvidence
) -> None:
    """Keep *evidence* on its change row."""
    from sqlalchemy import update

    from repowise.core.persistence.crud.changes import dump_evidence
    from repowise.core.persistence.models import Change

    await session.execute(
        update(Change)
        .where(Change.repository_id == repo_id, Change.change_id == change_id)
        .values(evidence_json=dump_evidence(evidence.to_dict()))
    )


async def change_evidence(
    session: Any,
    repo_id: str,
    repo_root: Path | str,
    row: Any,
    *,
    refresh: bool = False,
    risk: Any = None,
    health: Any = None,
    key: OutcomesKey | None = None,
    catalog: str | None = None,
) -> ChangeEvidence:
    """The stored change's checklist: the kept one, or built and kept now.

    A kept one whose outcomes were read at another HEAD has that item read
    again and merged into what is stored (:func:`keep_outcomes`). *key* is the
    HEAD and window to read at, when the caller already has them. A kept one
    resolved under another component catalog has its components resolved
    again (:func:`with_current_components`); *catalog* is the catalog's
    digest now, when the caller already has it.
    """
    from repowise.core.persistence.crud.changes import dump_evidence

    if not refresh and (cached := cached_evidence(row)) is not None:
        key = key or await outcomes_key(repo_root)
        if await outcomes_stale(repo_root, cached, key):
            item = await outcomes_item(session, repo_id, repo_root, row, key)
            await session.flush()
            merged = await keep_outcomes(session, repo_id, row.change_id, item)
            session.expire(row, ["evidence_json"])
            cached = merged or with_item(cached, item)
        return await with_current_components(
            session, repo_id, repo_root, row, cached, catalog=catalog
        )
    evidence = await build_stored_evidence(
        session, repo_id, repo_root, row, risk=risk, health=health
    )
    row.evidence_json = dump_evidence(evidence.to_dict())
    await session.flush()
    return evidence


async def stored_change_at(
    session: Any, repo_id: str, repo_root: Path | str, revspec: str | None
) -> Any:
    """The stored change whose head commit *revspec* names, or ``None``."""
    from repowise.core.git_refs import resolve, split_revspec
    from repowise.core.persistence.crud import get_change

    if not revspec or split_revspec(revspec) is not None:
        return None
    sha = await asyncio.to_thread(resolve, str(repo_root), revspec)
    if not sha:
        return None
    row = await get_change(session, repo_id, sha)
    return row if row is not None and row.head_sha == sha else None


#: ``(HEAD sha, window days)``: what a kept ``outcomes`` item was read at.
OutcomesKey = tuple[str, int]


async def outcomes_key(repo_root: Path | str) -> OutcomesKey:
    """The HEAD and window outcomes are read at now; ``""`` when there is no HEAD."""
    from repowise.core.changes.outcomes import fix_days
    from repowise.core.git_refs import resolve

    head = await asyncio.to_thread(resolve, str(repo_root), "HEAD")
    return head, fix_days(repo_root)


def _stamp(item: EvidenceItem | None) -> OutcomesKey | None:
    if item is None:
        return None
    stamp = item.value if isinstance(item.value, dict) else (item.details or [{}])[0]
    return stamp.get("as_of"), stamp.get("days")


async def outcomes_stale(
    repo_root: Path | str, evidence: ChangeEvidence, key: OutcomesKey | None = None
) -> bool:
    """Whether the kept ``outcomes`` item was read at another HEAD or window,
    or is missing (a checklist kept before the item existed)."""
    key = key or await outcomes_key(repo_root)
    return _stamp(evidence.item("outcomes")) != key


async def outcomes_item(
    session: Any, repo_id: str, repo_root: Path | str, row: Any, key: OutcomesKey
) -> EvidenceItem:
    """The ``outcomes`` item for the stored change *row*, read at *key*."""
    from repowise.core.changes.evidence import _outcomes

    found = await _guard(_change_outcomes(session, repo_id, Path(repo_root), row, key))
    return _outcomes(EvidenceInputs(outcomes=found))


def with_item(evidence: ChangeEvidence, *items: EvidenceItem) -> ChangeEvidence:
    """*evidence* with each of *items* in place of its item of that kind, or added
    in :data:`~.evidence.KINDS` order."""
    new = {i.kind: i for i in items}
    merged = [new.pop(i.kind, i) for i in evidence.items] + list(new.values())
    rank = {kind: n for n, kind in enumerate(KINDS)}
    merged.sort(key=lambda i: rank.get(i.kind, len(rank)))
    return replace(evidence, items=merged)


async def with_current_outcomes(
    session: Any,
    repo_id: str,
    repo_root: Path | str,
    row: Any,
    evidence: ChangeEvidence,
    *,
    key: OutcomesKey | None = None,
) -> ChangeEvidence:
    """*evidence* with its ``outcomes`` item read at HEAD; the same object when it already was."""
    key = key or await outcomes_key(repo_root)
    if not await outcomes_stale(repo_root, evidence, key):
        return evidence
    item = await outcomes_item(session, repo_id, repo_root, row, key)
    if item == evidence.item("outcomes"):
        return evidence
    return with_item(evidence, item)


async def keep_outcomes(
    session: Any, repo_id: str, change_id: str, item: EvidenceItem
) -> ChangeEvidence | None:
    """Merge *item* into the checklist stored now, touching no other item.

    The stored checklist is read again here, so a full rebuild kept while
    *item* was being read is not overwritten; one already holding outcomes
    read at the same HEAD and window is left as it is. ``None`` when no
    checklist is stored.
    """
    return await _merge_kept(
        session,
        repo_id,
        change_id,
        lambda stored: (
            None if _stamp(stored.item("outcomes")) == _stamp(item) else with_item(stored, item)
        ),
    )


async def _merge_kept(
    session: Any, repo_id: str, change_id: str, merge: Any
) -> ChangeEvidence | None:
    """Read the checklist stored now, keep ``merge(stored)`` unless it is ``None``
    (nothing to change), and return what is kept. ``None`` when none is stored."""
    from sqlalchemy import select, update

    from repowise.core.persistence.crud.changes import dump_evidence
    from repowise.core.persistence.models import Change

    where = (Change.repository_id == repo_id, Change.change_id == change_id)
    raw = (await session.execute(select(Change.evidence_json).where(*where))).scalar_one_or_none()
    stored = cached_evidence(SimpleNamespace(evidence_json=raw))
    if stored is None:
        return None
    merged = merge(stored)
    if merged is None:
        return stored
    await session.execute(
        update(Change).where(*where).values(evidence_json=dump_evidence(merged.to_dict()))
    )
    return merged


#: Why a kept checklist's components are not read under the catalog now.
CATALOG_CHANGED = (
    "not resolved against the current component catalog, and the change's file list "
    "is not kept in full; --refresh rebuilds it"
)


async def components_stale(
    repo_root: Path | str, evidence: ChangeEvidence, catalog: str | None = None
) -> bool:
    """Whether *evidence* was resolved under another component catalog, or before
    the catalog was recorded on it."""
    catalog = catalog or await asyncio.to_thread(catalog_digest, repo_root)
    return evidence.catalog_digest != catalog


def _stored_paths(row: Any) -> list[str] | None:
    """The change's files as kept on its row; ``None`` when the list was capped."""
    try:
        files = json.loads(row.files_json or "[]")
    except (TypeError, ValueError):
        return None
    if len(files) < (row.files_changed or 0):
        return None
    return [f["path"] for f in files if isinstance(f, dict) and f.get("path")]


async def with_current_components(
    session: Any,
    repo_id: str,
    repo_root: Path | str,
    row: Any,
    evidence: ChangeEvidence,
    *,
    catalog: str | None = None,
) -> ChangeEvidence:
    """*evidence* with its components and owners resolved under the catalog now.

    Path globs are cheap, so a catalog edit is applied on read from the files
    kept on the change's row, and kept. When that list was capped the catalog
    is stamped as read: a checklist kept before digests were recorded keeps its
    components, and one resolved under another catalog has its components item
    kept as unknown, naming ``--refresh``.
    """
    catalog = catalog or await asyncio.to_thread(catalog_digest, repo_root)
    if evidence.catalog_digest == catalog:
        return evidence
    paths = _stored_paths(row)
    if paths is None:
        return await _stamp_capped(session, repo_id, row, evidence, catalog)
    return await _resolve_kept(session, repo_id, repo_root, row, evidence, catalog, paths)


async def _stamp_capped(
    session: Any, repo_id: str, row: Any, evidence: ChangeEvidence, catalog: str
) -> ChangeEvidence:
    """A capped checklist under *catalog*: kept, so the next read does no work."""
    from repowise.core.changes.evidence import _unknown

    item = _unknown("components_touched", "Components touched", CATALOG_CHANGED, "components")
    # No digest: built before digests were recorded, likely under this catalog.
    items = () if evidence.catalog_digest is None else (item,)
    return await _keep_resolved(session, repo_id, row, evidence, catalog, items)


async def _keep_resolved(
    session: Any,
    repo_id: str,
    row: Any,
    evidence: ChangeEvidence,
    catalog: str,
    items: Sequence[EvidenceItem],
) -> ChangeEvidence:
    """*items* merged into what is stored, stamped with *catalog*, and kept."""
    current = replace(with_item(evidence, *items), catalog_digest=catalog)
    await session.flush()
    kept = await _merge_kept(
        session,
        repo_id,
        row.change_id,
        lambda stored: (
            None
            if stored.catalog_digest == catalog
            else replace(with_item(stored, *items), catalog_digest=catalog)
        ),
    )
    session.expire(row, ["evidence_json"])
    return kept or current


async def _resolve_kept(
    session: Any,
    repo_id: str,
    repo_root: Path | str,
    row: Any,
    evidence: ChangeEvidence,
    catalog: str,
    paths: list[str],
) -> ChangeEvidence:
    """Components and owners resolved again over *paths*, and kept."""
    from repowise.core.changes.evidence import _components as components_item
    from repowise.core.changes.evidence import _owners as owners_item

    root = Path(repo_root)
    components = await _guard(_components(session, repo_id, root, paths))
    owners, sources = await _guard_pair(_owners(root, paths, components))
    code = _code_paths(paths)
    inputs = EvidenceInputs(
        code_paths=code, components=components, owners=owners, owner_sources=sources
    )
    items = (components_item(inputs), owners_item(inputs, code))
    if isinstance(components, Unavailable):
        return with_item(evidence, *items)  # not kept: the next read tries again
    return await _keep_resolved(session, repo_id, row, evidence, catalog, items)


def cached_evidence(row: Any) -> ChangeEvidence | None:
    """The kept checklist, or ``None`` when there is none in this schema."""
    raw = getattr(row, "evidence_json", None)
    if not raw:
        return None
    try:
        evidence = ChangeEvidence.from_dict(json.loads(raw))
    except (TypeError, ValueError, KeyError):
        return None
    return evidence if evidence.schema_version == EVIDENCE_SCHEMA_VERSION else None


def evidence_summary(row: Any) -> dict[str, Any] | None:
    """Status counts of the kept checklist, or ``None`` when none is kept.

    Kinds in ``SUMMARY_SKIPPED_KINDS`` are not counted, so the list's attention
    mark and the needs-attention filter agree on one set of rows.
    """
    evidence = cached_evidence(row)
    if evidence is None:
        return None
    counts = {"generated_at": evidence.generated_at, "ok": 0, "attention": 0, "unknown": 0}
    for item in evidence.items:
        if item.kind in SUMMARY_SKIPPED_KINDS:
            continue
        # A status this reader does not know is not a pass.
        counts[item.status if item.status in ("ok", "attention") else "unknown"] += 1
    return counts


async def list_needing_attention(
    session: Any,
    repo_id: str,
    where: ChangeFilter | None = None,
    *,
    limit: int | None,
    offset: int = 0,
) -> tuple[list[Any], int]:
    """Changes matching *where* whose kept checklist flags attention, newest
    merged first, and their total. Reads kept checklists only, never builds one.
    """
    from repowise.core.persistence.crud import (
        ChangeFilter,
        list_attention_candidates,
        list_changes,
    )

    # Checklists are built on first view, so the prefiltered set stays small;
    # confirm on the parsed evidence, then page here. Ceiling: if checklists
    # are ever built in bulk, move this filter into SQL.
    candidates = await list_attention_candidates(session, repo_id, where)
    ids = [r.change_id for r in candidates if (evidence_summary(r) or {}).get("attention")]
    page = ids[offset:] if limit is None else ids[offset : offset + limit]
    rows, _ = await list_changes(session, repo_id, ChangeFilter(change_ids=page), limit=None)
    return rows, len(ids)


# -- inputs -------------------------------------------------------------------


async def gather_inputs(
    session: Any,
    repo_id: str,
    repo_root: Path,
    paths: list[str],
    *,
    shas: list[str],
    row: Any = None,
) -> EvidenceInputs:
    """Index facts for *paths* and the change's commits *shas*.

    A source that fails reads as ``Unavailable``, with the reason.
    """
    components = await _guard(_components(session, repo_id, repo_root, paths))
    owners, owner_sources = await _guard_pair(_owners(repo_root, paths, components))
    contracts, consumers = await _guard_pair(asyncio.to_thread(_workspace, repo_root, paths))
    return EvidenceInputs(
        code_paths=_code_paths(paths),
        components=components,
        owners=owners,
        owner_sources=owner_sources,
        reviewers=await _guard(_reviewers(session, repo_id, repo_root, paths, shas)),
        contracts=contracts,
        consumers=consumers,
        decisions=await _guard(_decisions(session, repo_id, paths)),
        provenance=await _guard(_provenance(session, repo_id, repo_root, shas, row)),
        outcomes=(
            await _guard(
                _change_outcomes(session, repo_id, repo_root, row, await outcomes_key(repo_root))
            )
            if row is not None
            else Unavailable("not a merged change in the index")
        ),
    )


async def _guard(coro: Any) -> Any:
    try:
        return await coro
    except Exception as exc:
        logger.debug("evidence_input_failed", error=str(exc))
        return Unavailable(f"could not be read: {type(exc).__name__}: {exc}")


async def _guard_pair(coro: Any) -> tuple[Any, Any]:
    got = await _guard(coro)
    return (got, got) if isinstance(got, Unavailable) else got


def _code_paths(paths: list[str]) -> list[str]:
    from repowise.core.ingestion.languages.registry import REGISTRY
    from repowise.core.test_paths import is_test_related_path

    suffixes = REGISTRY.non_infra_code_extensions()
    return [p for p in paths if Path(p).suffix in suffixes and not is_test_related_path(p)]


async def _components(
    session: Any, repo_id: str, repo_root: Path, paths: list[str]
) -> list[dict[str, Any]]:
    from repowise.core.components import resolve_components
    from repowise.core.components.catalog import owner_group_name
    from repowise.core.components.store import component_rollups, load_repo_components

    loaded = await load_repo_components(session, repo_id, repo_root)
    known = loaded.resolved.components
    # Resolved again over the change's own paths, which the index may no
    # longer hold (a later change deleted them).
    mine = resolve_components(
        [c for c in known if c.declared], [c for c in known if not c.declared], paths
    )
    touched = [c for c in mine.components if mine.files.get(c.name)]
    # The bus factor is the whole component's, not just the changed files'.
    rollups = await component_rollups(
        session,
        repo_id,
        {c.name: loaded.resolved.files.get(c.name) or mine.files[c.name] for c in touched},
        loaded.test_files,
    )
    rows = []
    for c in touched:
        bus = (rollups.get(c.name) or {}).get("bus_factor") or {}
        rows.append(
            {
                "name": c.name,
                "source": c.source,
                "owner": owner_group_name(c.owner),
                "criticality": c.criticality,
                "tags": list(c.tags),
                "files": mine.files[c.name],
                "bus_factor": int(bus["value"]) if bus.get("basis") == "measured" else None,
            }
        )
    if mine.unassigned:
        # Resolution ran and these files belong to no component: a known answer.
        catalog = "declared" if loaded.catalog.components else "derived"
        rows.append({"outside": True, "source": catalog, "files": mine.unassigned})
    return rows


async def _owners(
    repo_root: Path, paths: list[str], components: Any
) -> tuple[dict[str, list[str]], list[str]]:
    """``{path: owners}`` from CODEOWNERS (per section, last match wins), else the component owner."""
    sources: list[str] = []
    matcher: CodeownersMatcher | None = None
    if found := read_codeowners(repo_root):
        rel, text = found
        matcher = CodeownersMatcher(parse_codeowners(text))
        sources.append(rel)
    by_component: dict[str, str] = {}
    if not isinstance(components, Unavailable):
        for comp in components:
            if comp.get("owner"):
                for path in comp["files"]:
                    by_component[path] = comp["owner"]
        if by_component:
            sources.append("component catalog")
    owners: dict[str, list[str]] = {}
    for path in paths:
        named = matcher.owners(path) if matcher else ()
        if named:
            owners[path] = list(named)
        elif path in by_component:
            owners[path] = [by_component[path]]
    return owners, sources


async def _reviewers(
    session: Any, repo_id: str, repo_root: Path, paths: list[str], shas: list[str]
) -> list[dict[str, Any]] | Unavailable:
    """People with history in these files, by name, leaving out the change's own."""
    from sqlalchemy import select

    from repowise.core.analysis.reviewers import cochange_paths, suggest_reviewers
    from repowise.core.git_refs import is_shallow
    from repowise.core.persistence.models import GitMetadata

    if await asyncio.to_thread(is_shallow, str(repo_root)):
        return Unavailable("a shallow clone cuts off the history ownership is read from")

    async def rows(wanted: set[str] | list[str]) -> list[Any]:
        if not wanted:
            return []
        q = select(GitMetadata).where(
            GitMetadata.repository_id == repo_id, GitMetadata.file_path.in_(list(wanted))
        )
        return list((await session.execute(q)).scalars().all())

    direct = await rows(paths)
    own = await asyncio.to_thread(_change_people, str(repo_root), shas)
    candidates = suggest_reviewers(direct, await rows(cochange_paths(direct)), limit=50)
    kept = [
        s
        for s in candidates
        if (s["email"] or "").strip().lower() not in own
        and (s["name"] or "").strip().lower() not in own
    ][:REVIEWER_LIMIT]
    # Listed by name: the order says nothing about who matters more.
    return sorted(
        (
            {
                "name": s["name"],
                "paths": s["owned_paths"],
                "reasons": sorted({_REVIEWER_REASONS.get(r, r) for r in s["reasons"]}),
            }
            for s in kept
        ),
        key=lambda r: r["name"].casefold(),
    )


def _change_people(repo_root: str, shas: list[str]) -> set[str]:
    """Lower-cased names and emails of the change's authors and co-authors."""
    from repowise.core.git_refs import _read

    if not shas:
        return set()
    raw = _read(
        repo_root,
        [
            "log",
            "--no-walk=unsorted",
            "--format=%an%x1f%ae%x1f%(trailers:key=Co-authored-by,valueonly,separator=%x1e)%x1d",
            *shas,
        ],
    )
    people: set[str] = set()
    for record in raw.split("\x1d"):
        parts = record.strip().split("\x1f")
        if len(parts) < 2:
            continue
        people.update({parts[0], parts[1]})
        for trailer in (parts[2] if len(parts) > 2 else "").split("\x1e"):
            if m := _CO_AUTHOR.match(trailer):
                people.update(m.groups())
    return {p.strip().lower() for p in people if p and p.strip()}


def _workspace(
    repo_root: Path, paths: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | tuple[Unavailable, Unavailable]:
    """Workspace contracts in *paths*, and the other repos consuming them."""
    from repowise.core.workspace.config import WorkspaceConfig, find_workspace_root
    from repowise.core.workspace.contracts import load_contract_store

    none = Unavailable("the repository is not in a workspace with a contract map")
    ws_root = find_workspace_root(repo_root)
    if ws_root is None:
        return none, none
    root = repo_root.resolve()
    config = WorkspaceConfig.load(ws_root)
    alias = next((r.alias for r in config.repos if (ws_root / r.path).resolve() == root), None)
    store = load_contract_store(ws_root)
    if alias is None or store is None:
        return none, none
    touched = set(paths)
    contracts = [
        {
            "path": c.file_path,
            "contract_id": c.contract_id,
            "type": c.contract_type,
            "role": c.role,
        }
        for c in store.contracts
        if c.repo == alias and c.file_path in touched
    ]
    consumers = [
        {
            "repo": lk.consumer_repo,
            "file": lk.consumer_file,
            "contract_id": lk.contract_id,
            "provider_file": lk.provider_file,
        }
        for lk in store.contract_links
        if lk.provider_repo == alias
        and lk.provider_file in touched
        and lk.consumer_repo != alias
    ]
    return contracts, consumers


async def _decisions(session: Any, repo_id: str, paths: list[str]) -> list[dict[str, Any]]:
    """Decisions linked to a changed file itself; a module link is not a file's."""
    from sqlalchemy import select

    from repowise.core.persistence.crud.authority import accepted_decision_ids
    from repowise.core.persistence.models import DecisionNodeLink, DecisionRecord

    if not paths:
        return []
    rows = (
        await session.execute(
            select(
                DecisionRecord.id,
                DecisionRecord.title,
                DecisionRecord.status,
                DecisionNodeLink.node_id,
            )
            .join(DecisionNodeLink, DecisionNodeLink.decision_id == DecisionRecord.id)
            .where(
                DecisionNodeLink.repository_id == repo_id,
                DecisionNodeLink.link_type == "file",
                DecisionNodeLink.node_id.in_(paths),
            )
        )
    ).all()
    accepted = await accepted_decision_ids(session, repo_id, governing_only=True)
    out: dict[str, dict[str, Any]] = {}
    for did, title, status, node in rows:
        if status in _RETIRED:
            continue
        slot = out.setdefault(
            did,
            {
                "id": did,
                "title": title,
                "authority": "accepted" if did in accepted else "candidate",
                "status": status,
                "paths": [],
            },
        )
        slot["paths"].append(node)
    return sorted(out.values(), key=lambda r: (r["authority"] != "accepted", r["title"]))


async def _provenance(
    session: Any, repo_id: str, repo_root: Path, shas: list[str], row: Any
) -> dict[str, Any] | Unavailable:
    """Three-state provenance over the change's commits, with one row per commit."""
    from sqlalchemy import select

    from repowise.core.attribution import UNKNOWN, attribution_enabled, commit_label
    from repowise.core.changes.record import provenance_block
    from repowise.core.changes.store import change_provenance
    from repowise.core.persistence.models import GitCommit

    if not attribution_enabled(repo_root):
        return Unavailable("AI attribution is off for this repository")
    if not shas:
        return Unavailable("uncommitted work carries no commit markers")
    stored: dict[str, tuple[str | None, str | None, int | None, str | None]] = {}
    for i in range(0, len(shas), 500):
        found = await session.execute(
            select(
                GitCommit.sha,
                GitCommit.attribution,
                GitCommit.agent_name,
                GitCommit.agent_autonomy_tier,
                GitCommit.agent_model_id,
            ).where(GitCommit.repository_id == repo_id, GitCommit.sha.in_(shas[i : i + 500]))
        )
        for sha, attribution, agent, tier, model in found.tuples():
            stored[sha] = (attribution, agent, tier, model)
    commits = []
    for sha in shas:
        attribution, agent, tier, model = stored.get(sha, (None, None, None, None))
        label = commit_label(attribution, agent) if sha in stored else UNKNOWN
        entry = {"sha": sha, "label": label, "tier": tier if label == "agent" else None}
        if label == "agent":
            entry.update({k: v for k, v in (("agent", agent), ("model", model)) if v})
        commits.append(entry)
    block = None
    if row is not None and row.provenance is not None:
        block = provenance_block(
            row.provenance, row.provenance_agent, row.provenance_known, len(shas)
        )
    if block is None:
        labels = {sha: stored[sha][:2] for sha in stored}
        block = provenance_block(*change_provenance(shas, labels), len(shas))
    if block is None:
        return Unavailable("no attribution was stored for these commits")
    return {**block, "commits": commits}


async def _change_outcomes(
    session: Any, repo_id: str, repo_root: Path, row: Any, key: OutcomesKey
) -> dict[str, Any]:
    """Links to the change from later history, and its files' later fix rate.

    What git cannot answer reads unknown with the reason, stamped with *key*.
    """
    from repowise.core.changes.outcomes import (
        OutcomeUnknownError,
        file_fix_rate,
        outcome_links,
        target_of,
    )
    from repowise.core.git_refs import is_shallow
    from repowise.core.persistence.crud import get_change

    head, days = key
    stamp = {"as_of": head, "days": days}
    if not head:
        return {**stamp, "unknown": "the repository has no HEAD commit"}
    if await asyncio.to_thread(is_shallow, str(repo_root)):
        return {**stamp, "unknown": "a shallow clone cuts off the history outcomes are read from"}
    target = target_of(row)
    try:
        links = await asyncio.to_thread(outcome_links, repo_root, head, target)
    except OutcomeUnknownError as exc:
        return {**stamp, "unknown": str(exc)}
    rows = []
    for link in links:
        later = await get_change(session, repo_id, link.sha)
        rows.append({**link.to_dict(), "change_id": later.change_id if later else None})
    try:
        rate = await asyncio.to_thread(file_fix_rate, repo_root, head, target, days)
        rate_unknown = None
    except OutcomeUnknownError as exc:
        rate, rate_unknown = None, str(exc)
    return {
        **stamp,
        "links": rows,
        "file_fix_rate": rate,
        "file_fix_rate_unknown": rate_unknown,
    }


def _change_commits(repo_root: str, revspec: str | None, row: Any) -> list[str]:
    """The change's commits: the stored list, else those *revspec* names."""
    from repowise.core.git_refs import _read, split_revspec

    if row is not None:
        try:
            return list(json.loads(row.commit_shas_json or "[]"))
        except (TypeError, ValueError):
            return [row.head_sha]
    if not revspec:
        return []
    parts = split_revspec(revspec)
    span = f"{parts[0]}..{parts[2]}" if parts else f"{revspec}^!"
    return _read(repo_root, ["rev-list", span]).split()


__all__ = [
    "NO_INDEX",
    "REVIEWER_LIMIT",
    "build_stored_evidence",
    "cached_evidence",
    "change_evidence",
    "components_stale",
    "evidence_for_revspec",
    "gather_inputs",
    "keep_outcomes",
    "outcomes_item",
    "outcomes_key",
    "outcomes_stale",
    "save_evidence",
    "stored_change_at",
    "with_current_components",
    "with_current_outcomes",
    "with_item",
]
