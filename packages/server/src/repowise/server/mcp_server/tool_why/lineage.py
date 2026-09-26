"""Decision lineage: the supersedes / refines chain a record sits in."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import DecisionEdge, DecisionRecord

#: Records one chain may visit. A guard against a cycle the visited set misses,
#: not a depth anyone expects a real lineage to reach.
_MAX_LINEAGE_HOPS = 50


async def _outgoing_lineage_edges(
    session: Any, repository_id: Any
) -> dict[str, list[Any]]:
    """Every supersedes / refines edge, grouped by source, strongest first."""
    edges = list(
        (
            await session.execute(
                select(DecisionEdge).where(
                    DecisionEdge.repository_id == repository_id,
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
    return outgoing


async def _lineage_records(
    session: Any, outgoing: dict[str, list[Any]], all_decisions: list[Any]
) -> dict[str, Any]:
    """Records by id for every edge endpoint, loading only those not in hand."""
    records = {record.id: record for record in all_decisions}
    edge_record_ids = {
        decision_id
        for rows in outgoing.values()
        for edge in rows
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
    return records


def _walk_lineage(
    start_id: str, outgoing: dict[str, list[Any]]
) -> list[tuple[str, str | None]]:
    """``(decision_id, relation)`` from *start_id* along the strongest unvisited edge."""
    order: list[tuple[str, str | None]] = []
    visited: set[str] = set()
    current = start_id
    relation: str | None = None
    while current and current not in visited and len(order) < _MAX_LINEAGE_HOPS:
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
    return order


def _lineage_chain(
    order: list[tuple[str, str | None]], records: dict[str, Any]
) -> list[dict]:
    """The walk as rows, root first, skipping ids no record backs."""
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
    return chain


async def _lineage_for_records(
    session: Any, candidates: list[Any], all_decisions: list[Any]
) -> dict[str, list[dict]]:
    """Build every candidate lineage from one edge query and in-memory records."""
    if not candidates:
        return {}
    outgoing = await _outgoing_lineage_edges(session, candidates[0].repository_id)
    records = await _lineage_records(session, outgoing, all_decisions)
    result: dict[str, list[dict]] = {}
    for candidate in candidates:
        order = _walk_lineage(candidate.id, outgoing)
        if len(order) <= 1:
            continue
        chain = _lineage_chain(order, records)
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
