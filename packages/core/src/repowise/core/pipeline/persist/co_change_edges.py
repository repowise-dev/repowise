"""Bring the stored ``co_changes`` edges in line with the rebuilt graph on update.

Co-change pairs come from the repo-wide history walk, so one new commit can add
or drop a pair between two files it never touched. The changed-file edge
reconcile cannot see those pairs; this step diffs every stored pair instead.
"""

from __future__ import annotations

from typing import Any

import structlog

from .prune import _PRUNE_CHUNK

logger = structlog.get_logger(__name__)

_CO_CHANGES = "co_changes"


def _graph_co_change_edges(graph_builder: Any) -> dict[tuple[str, str], dict]:
    from .graph import _edge_row

    return {
        (u, v): _edge_row(u, v, data)
        for u, v, data in graph_builder.graph().edges(data=True)
        if data.get("edge_type") == _CO_CHANGES
    }


async def sync_co_change_edges(session: Any, repo_id: str, graph_builder: Any) -> None:
    """Delete stored ``co_changes`` rows the graph no longer has and insert the new ones.

    A diff rather than a rewrite: on a typical update few pairs move, and a large
    repo stores a six-figure number of them.
    """
    from sqlalchemy import delete, select

    from repowise.core.persistence.crud.graph import _edge_values
    from repowise.core.persistence.models import GraphEdge

    fresh = _graph_co_change_edges(graph_builder)
    rows = await session.execute(
        select(GraphEdge.id, GraphEdge.source_node_id, GraphEdge.target_node_id).where(
            GraphEdge.repository_id == repo_id, GraphEdge.edge_type == _CO_CHANGES
        )
    )
    stale: list[str] = []
    stored: set[tuple[str, str]] = set()
    for row_id, source, target in rows:
        pair = (source, target)
        # A repeated pair is stale too, so a store that drifted converges to one row.
        if pair in fresh and pair not in stored:
            stored.add(pair)
        else:
            stale.append(row_id)
    for i in range(0, len(stale), _PRUNE_CHUNK):
        await session.execute(
            delete(GraphEdge).where(GraphEdge.id.in_(stale[i : i + _PRUNE_CHUNK]))
        )
    added = [edge for pair, edge in fresh.items() if pair not in stored]
    for edge in added:
        session.add(GraphEdge(**_edge_values(repo_id, edge)))
    await session.flush()
    if stale or added:
        logger.info("co_change_edges_synced", repo_id=repo_id, removed=len(stale), added=len(added))
