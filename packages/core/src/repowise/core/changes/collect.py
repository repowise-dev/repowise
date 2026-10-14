"""Gather a change's evidence from git and the index, and keep it on its row.

:func:`evidence_for_revspec` reviews any commit, range or the working tree;
:func:`build_stored_evidence` does it for a stored Change Record without
writing, :func:`save_evidence` keeps the result on ``changes.evidence_json``,
and :func:`change_evidence` does both. All end in
:func:`~repowise.core.changes.evidence.build_change_evidence`.

A review costs seconds per change (the health comparison parses both sides),
so it is computed on first read, never during an index or update, and kept:
a merged change's checklist is a snapshot stamped with ``generated_at`` and
the ``index_commit`` it read, rebuilt only on request (``refresh``).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import structlog

from repowise.core.changes.evidence import (
    EVIDENCE_SCHEMA_VERSION,
    ChangeEvidence,
    EvidenceInputs,
    Unavailable,
    build_change_evidence,
)
from repowise.core.ownership.codeowners import (
    CodeownersMatcher,
    parse_codeowners,
    read_codeowners,
)

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
        index_commit = None
    else:
        shas = await asyncio.to_thread(_change_commits, root, revspec, row)
        inputs = await gather_inputs(
            session,
            repo_id,
            Path(root),
            [e.path for e in bundle.manifest],
            shas=shas,
            row=row,
        )
        index_commit = getattr(await get_repository(session, repo_id), "head_commit", None)
    return build_change_evidence(
        bundle, inputs, change_id=change_id, revspec=revspec, index_commit=index_commit
    )


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

    from repowise.core.persistence.models import Change

    await session.execute(
        update(Change)
        .where(Change.repository_id == repo_id, Change.change_id == change_id)
        .values(evidence_json=json.dumps(evidence.to_dict()))
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
) -> ChangeEvidence:
    """The stored change's checklist: the kept one, or built and kept now."""
    if not refresh and (cached := cached_evidence(row)) is not None:
        return cached
    evidence = await build_stored_evidence(
        session, repo_id, repo_root, row, risk=risk, health=health
    )
    row.evidence_json = json.dumps(evidence.to_dict())
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
    stored: dict[str, tuple[str | None, str | None, int | None]] = {}
    for i in range(0, len(shas), 500):
        found = await session.execute(
            select(
                GitCommit.sha,
                GitCommit.attribution,
                GitCommit.agent_name,
                GitCommit.agent_autonomy_tier,
            ).where(GitCommit.repository_id == repo_id, GitCommit.sha.in_(shas[i : i + 500]))
        )
        for sha, attribution, agent, tier in found.tuples():
            stored[sha] = (attribution, agent, tier)
    commits = []
    for sha in shas:
        attribution, agent, tier = stored.get(sha, (None, None, None))
        label = commit_label(attribution, agent) if sha in stored else UNKNOWN
        commits.append({"sha": sha, "label": label, "tier": tier if label == "agent" else None})
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
    "evidence_for_revspec",
    "gather_inputs",
    "save_evidence",
    "stored_change_at",
]
