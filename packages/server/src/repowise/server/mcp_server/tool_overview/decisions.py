"""The accepted-decisions block and its recent reversals."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from repowise.core.persistence.crud import accepted_predicate
from repowise.core.persistence.models import DecisionEdge, DecisionRecord
from repowise.server.mcp_server._helpers import decision_is_excluded


async def _build_recent_reversals(session: Any, repository: Any) -> list[dict[str, Any]]:
    """Recent supersede edges resolved to newer/older decision title pairs."""
    supersede_edges_res = await session.execute(
        select(DecisionEdge)
        .where(
            DecisionEdge.repository_id == repository.id,
            DecisionEdge.kind == "supersedes",
        )
        .order_by(DecisionEdge.created_at.desc())
        .limit(5)
    )
    supersede_edges = supersede_edges_res.scalars().all()
    if not supersede_edges:
        return []
    all_edge_ids = list(
        {e.src_decision_id for e in supersede_edges} | {e.dst_decision_id for e in supersede_edges}
    )
    edge_recs_res = await session.execute(
        select(DecisionRecord).where(DecisionRecord.id.in_(all_edge_ids))
    )
    edge_recs = {r.id: r for r in edge_recs_res.scalars().all()}
    recent_reversals: list[dict[str, Any]] = []
    for edge in supersede_edges:
        src = edge_recs.get(edge.src_decision_id)
        dst = edge_recs.get(edge.dst_decision_id)
        if src and dst:
            recent_reversals.append(
                {
                    "newer": {"id": src.id, "title": src.title},
                    "older": {
                        "id": dst.id,
                        "title": dst.title,
                        "status": dst.status,
                    },
                }
            )
    return recent_reversals


async def _build_key_decisions(
    session: Any, repository: Any, exclude_spec: Any = None
) -> dict[str, Any]:
    """The repository's accepted decisions, and its recent reversals.

    ``accepted_predicate()`` rather than the status column: this block is the
    first thing an agent reads about a repository, so a machine-inferred
    record nobody agreed to must not appear under a heading that presents it
    as what the repository has settled on.
    """
    try:
        # Over-fetch, then drop records anchored entirely in excluded paths
        # (vendored venvs, local-only scratch dirs mined before the exclude
        # rules changed) so the repo's "top decisions" are never junk.
        top_decisions_res = await session.execute(
            select(DecisionRecord)
            .where(
                DecisionRecord.repository_id == repository.id,
                DecisionRecord.status == "active",
                accepted_predicate(),
            )
            .order_by(DecisionRecord.confidence.desc())
            .limit(25)
        )
        top_decisions = [
            dr
            for dr in top_decisions_res.scalars().all()
            if not decision_is_excluded(dr, exclude_spec)
        ][:5]
        if not top_decisions:
            return {}
        key_decisions_list = []
        for dr in top_decisions:
            try:
                affected_files = json.loads(dr.affected_files_json or "[]")[:3]
            except (json.JSONDecodeError, TypeError):
                affected_files = []
            key_decisions_list.append(
                {
                    "id": dr.id,
                    "title": dr.title,
                    "status": dr.status,
                    "confidence": dr.confidence,
                    "verification": dr.verification,
                    "affected_files": affected_files,
                }
            )
        return {
            "top_active": key_decisions_list,
            "recent_reversals": await _build_recent_reversals(session, repository),
        }
    except Exception:
        return {}
