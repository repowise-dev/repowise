"""Database reads the modes share: corpus, git metadata, lineage, evidence rows."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from repowise.core.persistence.crud.authority import (
    accepted_decision_ids,
)
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    DecisionEdge,
    DecisionEvidence,
    DecisionRecord,
    GitMetadata,
)
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    decision_is_excluded,
)
from repowise.server.mcp_server.tool_why.caps import _DECISION_CORPUS_LIMIT


async def _load_target_git(
    session: Any, repository_id: Any, targets: list[str] | None
) -> dict[str, Any]:
    """Load per-target git metadata keyed by file path (only present ones)."""
    target_git: dict[str, Any] = {}
    if not targets:
        return target_git
    for t in targets:
        git_res = await session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repository_id,
                GitMetadata.file_path == t,
            )
        )
        meta = git_res.scalar_one_or_none()
        if meta:
            target_git[t] = meta
    return target_git


async def _lineage_for_records(
    session: Any, candidates: list[Any], all_decisions: list[Any]
) -> dict[str, list[dict]]:
    """Build every candidate lineage from one edge query and in-memory records."""
    if not candidates:
        return {}
    edges = list(
        (
            await session.execute(
                select(DecisionEdge).where(
                    DecisionEdge.repository_id == candidates[0].repository_id,
                    DecisionEdge.kind.in_(("supersedes", "refines")),
                )
            )
        )
        .scalars()
        .all()
    )
    outgoing: dict[str, list[Any]] = {}
    for edge in edges:
        outgoing.setdefault(edge.src_decision_id, []).append(edge)
    for rows in outgoing.values():
        rows.sort(
            key=lambda edge: (
                edge.kind == "supersedes",
                edge.confidence or 0.0,
                edge.dst_decision_id,
            ),
            reverse=True,
        )
    records = {record.id: record for record in all_decisions}
    edge_record_ids = {
        decision_id
        for edge in edges
        for decision_id in (edge.src_decision_id, edge.dst_decision_id)
    }
    missing_ids = edge_record_ids - records.keys()
    if missing_ids:
        missing_records = list(
            (
                await session.execute(
                    select(DecisionRecord).where(DecisionRecord.id.in_(missing_ids))
                )
            )
            .scalars()
            .all()
        )
        records.update({record.id: record for record in missing_records})
    result: dict[str, list[dict]] = {}
    for candidate in candidates:
        order: list[tuple[str, str | None]] = []
        visited: set[str] = set()
        current = candidate.id
        relation: str | None = None
        while current and current not in visited and len(order) < 50:
            visited.add(current)
            order.append((current, relation))
            edge = next(
                (
                    row
                    for row in outgoing.get(current, [])
                    if row.dst_decision_id not in visited
                ),
                None,
            )
            if edge is None:
                break
            current = edge.dst_decision_id
            relation = edge.kind
        if len(order) <= 1:
            continue
        chain = []
        for decision_id, kind in reversed(order):
            record = records.get(decision_id)
            if record is not None:
                chain.append(
                    {
                        "id": record.id,
                        "title": record.title,
                        "status": record.status,
                        "source": record.source,
                        "relation": kind,
                    }
                )
        if len(chain) > 1:
            result[candidate.id] = chain
    return result


async def _lineage_for_matches(
    ctx: Any, keyword_matches: list, all_decisions: list[Any]
) -> dict[str, list[dict]]:
    """Build all keyword lineages with one bounded edge query."""
    if not keyword_matches:
        return {}
    async with get_session(ctx.session_factory) as session3:
        return await _lineage_for_records(session3, keyword_matches, all_decisions)


async def _decision_corpus(session: Any, repository_id: str, exclude_spec: Any) -> list:
    """The rankable decision records of one repository.

    A record anchored entirely in excluded paths is noise for every mode, and a
    dismissed one is a tombstone, so both filters belong wherever a corpus is
    built. Takes a session rather than a context so the caller that also needs a
    repository row and git metadata still opens one. Shared with workspace search
    precisely because that path used to build its own corpus and got neither
    filter.
    """
    from repowise.core.persistence.crud import list_decisions as _list_decisions

    records = await _list_decisions(
        session, repository_id, include_proposed=True, limit=_DECISION_CORPUS_LIMIT
    )
    return [d for d in records if not decision_is_excluded(d, exclude_spec)]


async def _attach_decision_evidence(session: Any, records: list) -> None:
    """Attach all persisted provenance rows with one bounded query.

    ``DecisionRecord`` is the compatibility headline.  The child rows are the
    canonical accreted evidence and must travel with it when trust metadata is
    projected, without introducing an N+1 query in workspace or search modes.
    """
    if not records:
        return
    ids = [record.id for record in records]
    rows = list(
        (
            await session.execute(
                select(DecisionEvidence)
                .where(DecisionEvidence.decision_id.in_(ids))
                .order_by(
                    DecisionEvidence.source_rank.desc(),
                    DecisionEvidence.created_at.asc(),
                    DecisionEvidence.id.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    by_decision: dict[str, list[DecisionEvidence]] = {}
    for row in rows:
        by_decision.setdefault(row.decision_id, []).append(row)
    for record in records:
        record._why_evidence_rows = by_decision.get(record.id, [])


async def _attach_response_decision_evidence(
    session: Any, result: dict[str, Any], records: list
) -> None:
    """Hydrate the decision rows *result* currently carries.

    Shared by all three modes, so what it covers is whatever the caller has
    built by the time it runs. Search mode calls it before its cap, which
    means the whole projected pool rather than the served head. That is
    deliberate: the rows the cap sheds are written to the omission store, and
    they have to carry their evidence by then or recovery returns bodies with
    no provenance.
    """
    ids: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            candidate = value.get("id") or value.get("decision_id")
            if isinstance(candidate, str):
                ids.add(candidate)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result)
    await _attach_decision_evidence(
        session, [record for record in records if record.id in ids]
    )


async def _hydrate_response_decision_evidence(
    ctx: Any, result: dict[str, Any], records: list
) -> None:
    async with get_session(ctx.session_factory) as session:
        await _attach_response_decision_evidence(session, result, records)


async def _load_corpus(repo: str | None, targets: list[str] | None) -> tuple:
    """Repo context, corpus, git metadata for targets, and the acceptance set.

    The prologue both target-aware modes open with. Shared so the corpus is
    filtered once: a record anchored entirely in excluded paths is noise for
    every mode downstream, and a mode that skipped the filter would answer from
    a different store than its neighbour.
    """
    ctx = await _resolve_repo_context(repo)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        all_decisions = await _decision_corpus(session, repository.id, _get_exclude_spec(ctx.path))
        # ``governing_only`` drops the ones whose authority was withdrawn, so
        # what comes back is what still binds rather than what was ever signed.
        accepted = await accepted_decision_ids(
            session, repository.id, governing_only=True
        )
        # Load git metadata for targets (for origin context in results)
        target_git = await _load_target_git(session, repository.id, targets)
    return ctx, repository, all_decisions, target_git, accepted
