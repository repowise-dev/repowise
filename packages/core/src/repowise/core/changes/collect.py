"""Gather a change's evidence from git and the index, and cache it on its row.

:func:`evidence_for_revspec` reviews any commit, range or the working tree;
:func:`change_evidence` does it for a stored Change Record and keeps the
result on ``changes.evidence_json``. Both end in
:func:`~repowise.core.changes.evidence.build_change_evidence`.

A review costs seconds per change (the health comparison parses both sides),
so it is computed on first read, never during an index or update, and kept:
a merged change's checklist is a snapshot stamped with ``generated_at`` and
the ``index_commit`` it read, rebuilt only on request (``refresh``).
"""

from __future__ import annotations

import asyncio
import json
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

logger = structlog.get_logger(__name__)

#: Reviewers listed per change; more is no better than none.
REVIEWER_LIMIT = 5

#: Decision statuses that no longer bind anything.
_RETIRED = frozenset({"dismissed", "superseded", "deprecated"})


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
    provenance: dict[str, Any] | Unavailable | None = None,
    author_email: str | None = None,
    risk: Any = None,
    health: Any = None,
    extensions: Sequence[str] = (),
    exclude_patterns: Sequence[str] = (),
) -> ChangeEvidence:
    """The checklist for *revspec* (``None`` is the working tree).

    *risk* and *health* are results the caller already computed; omitted, the
    review computes them. *provenance* omitted is read from the stored commit
    labels of the range. Without a *session* (no index) only the git-derived
    items are filled.
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
        missing = None if session is not None else "the repository has no index"
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
    if missing:
        gone = Unavailable(missing)
        inputs = EvidenceInputs(
            components=gone,
            owners=gone,
            reviewers=gone,
            contracts=gone,
            consumers=gone,
            decisions=gone,
            provenance=provenance or gone,
        )
        index_commit = None
    else:
        if provenance is None:
            provenance = await _range_provenance(session, repo_id, root, revspec)
        inputs = await gather_inputs(
            session,
            repo_id,
            Path(root),
            [e.path for e in bundle.manifest],
            provenance=provenance,
            author_email=author_email,
        )
        index_commit = getattr(await get_repository(session, repo_id), "head_commit", None)
    return build_change_evidence(
        bundle, inputs, change_id=change_id, revspec=revspec, index_commit=index_commit
    )


# -- stored changes -------------------------------------------------------------


async def change_evidence(
    session: Any, repo_id: str, repo_root: Path | str, row: Any, *, refresh: bool = False
) -> ChangeEvidence:
    """The stored change's checklist: the kept one, or built and kept now.

    The change is its head commit against that commit's first parent.
    """
    if not refresh and (cached := cached_evidence(row)) is not None:
        return cached
    evidence = await evidence_for_revspec(
        session,
        repo_id,
        repo_root,
        row.head_sha,
        change_id=row.change_id,
        provenance=stored_provenance(row, repo_root),
        author_email=row.author_email,
    )
    row.evidence_json = json.dumps(evidence.to_dict())
    await session.flush()
    return evidence


def stored_provenance(row: Any, repo_root: Path | str) -> dict[str, Any] | Unavailable:
    """The stored change's provenance block, or why there is none."""
    from repowise.core.attribution import attribution_enabled
    from repowise.core.changes.record import change_record

    on = attribution_enabled(repo_root)
    block = change_record(row, attribution=on)["provenance"]
    if block is not None:
        return block
    return Unavailable(
        "no attribution was stored for this change"
        if on
        else "AI attribution is off for this repository"
    )


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
    provenance: dict[str, Any] | Unavailable | None = None,
    author_email: str | None = None,
) -> EvidenceInputs:
    """Index facts for *paths*. A source that fails reads as ``Unavailable``."""
    components = await _guard(_components(session, repo_id, repo_root, paths))
    owners, owner_sources = await _guard_pair(_owners(repo_root, paths, components))
    contracts, consumers = await _guard_pair(asyncio.to_thread(_workspace, repo_root, paths))
    return EvidenceInputs(
        code_paths=_code_paths(paths),
        components=components,
        owners=owners,
        owner_sources=owner_sources,
        reviewers=await _guard(_reviewers(session, repo_id, repo_root, paths, author_email)),
        contracts=contracts,
        consumers=consumers,
        decisions=await _guard(_decisions(session, repo_id, paths)),
        provenance=provenance,
    )


async def _guard(coro: Any) -> Any:
    try:
        return await coro
    except Exception as exc:
        logger.debug("evidence_input_failed", error=str(exc))
        return Unavailable(f"could not be read: {type(exc).__name__}")


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
    from repowise.core.components.store import load_repo_components

    loaded = await load_repo_components(session, repo_id, repo_root)
    known = loaded.resolved.components
    # Resolved again over the change's own paths, which the index may no
    # longer hold (a later change deleted them).
    mine = resolve_components(
        [c for c in known if c.declared], [c for c in known if not c.declared], paths
    )
    return [
        {
            "name": c.name,
            "source": c.source,
            "owner": c.owner,
            "criticality": c.criticality,
            "tags": list(c.tags),
            "files": mine.files[c.name],
        }
        for c in mine.components
        if mine.files.get(c.name)
    ]


async def _owners(
    repo_root: Path, paths: list[str], components: Any
) -> tuple[dict[str, list[str]], list[str]]:
    """``{path: owners}`` from CODEOWNERS (last match wins), else the component owner."""
    from repowise.core.analysis.patch_coverage.suggest import CODEOWNERS_PATHS
    from repowise.core.components.catalog import owner_group_name

    sources: list[str] = []
    rules: list[tuple[Any, list[str]]] = []
    for rel in CODEOWNERS_PATHS:
        file = repo_root / rel
        if file.is_file():
            rules = _codeowners(file.read_text(encoding="utf-8", errors="replace"))
            sources.append(rel)
            break
    by_component: dict[str, str] = {}
    if not isinstance(components, Unavailable):
        for comp in components:
            if group := owner_group_name(comp.get("owner")):
                for path in comp["files"]:
                    by_component[path] = group
        if by_component:
            sources.append("component catalog")
    owners: dict[str, list[str]] = {}
    for path in paths:
        named = next((o for spec, o in reversed(rules) if spec.match_file(path)), None)
        if named:
            owners[path] = named
        elif path in by_component:
            owners[path] = [by_component[path]]
    return owners, sources


def _codeowners(text: str) -> list[tuple[Any, list[str]]]:
    import pathspec

    from repowise.core.analysis.patch_coverage.suggest import _codeowners_rules

    out = []
    for pattern, owners in _codeowners_rules(text):
        try:
            spec = pathspec.PathSpec.from_lines("gitwildmatch", [pattern])
        except Exception:
            continue
        out.append((spec, owners))
    return out


async def _reviewers(
    session: Any, repo_id: str, repo_root: Path, paths: list[str], author_email: str | None
) -> list[dict[str, Any]] | Unavailable:
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
    ranked = suggest_reviewers(direct, await rows(cochange_paths(direct)), limit=REVIEWER_LIMIT + 1)
    author = (author_email or "").strip().lower()
    # Ordered best first; the score itself is left out, so no one is ranked by a number.
    return [
        {"name": s["name"], "paths": s["owned_paths"], "reasons": s["reasons"]}
        for s in ranked
        if not author or (s["email"] or "").strip().lower() != author
    ][:REVIEWER_LIMIT]


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
            "contract_id": c.contract_id,
            "type": c.contract_type,
            "role": c.role,
            "file": c.file_path,
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
            select(DecisionRecord.id, DecisionRecord.title, DecisionRecord.status,
                   DecisionNodeLink.node_id)
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


async def _range_provenance(
    session: Any, repo_id: str, repo_root: str, revspec: str | None
) -> dict[str, Any] | Unavailable:
    """Three-state provenance over the commits *revspec* names."""
    from repowise.core.attribution import attribution_enabled
    from repowise.core.changes.record import provenance_block
    from repowise.core.changes.store import change_provenance
    from repowise.core.git_refs import split_revspec
    from repowise.core.persistence.crud import get_commit_labels

    if not attribution_enabled(repo_root):
        return Unavailable("AI attribution is off for this repository")
    if not revspec:
        return Unavailable("uncommitted work carries no commit markers")
    parts = split_revspec(revspec)
    span = f"{parts[0]}..{parts[2]}" if parts else f"{revspec}^!"
    shas = await asyncio.to_thread(_rev_list, repo_root, span)
    if not shas:
        return Unavailable(f"git could not list the commits of {revspec!r}")
    labels = await get_commit_labels(session, repo_id, shas)
    label, agent, known = change_provenance(shas, labels)
    return provenance_block(label, agent, known, len(shas)) or Unavailable("no commits")


def _rev_list(repo_root: str, span: str) -> list[str]:
    from repowise.core.git_refs import _read

    return _read(repo_root, ["rev-list", span]).split()


__all__ = [
    "REVIEWER_LIMIT",
    "cached_evidence",
    "change_evidence",
    "evidence_for_revspec",
    "gather_inputs",
    "stored_change_at",
    "stored_provenance",
]
