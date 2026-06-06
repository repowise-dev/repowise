"""Full-graph export and node search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import GraphEdge, GraphNode
from repowise.server.deps import get_db_session
from repowise.server.routers.graph._common import _edge_response, _escape_like, with_repo
from repowise.server.routers.graph.signals import (
    _EMPTY_SIGNALS,
    _collect_node_signals,
    _to_graph_node,
)
from repowise.server.schemas import GraphExportResponse, NodeSearchResult

# Cap on full-graph export; above this we return top-N by PageRank with truncated=True.
# Sized to keep the client-side force layout responsive; clients can step the
# limit up via the truncation banner.
_FULL_GRAPH_NODE_CAP = 1500

router = APIRouter()


@router.get("/{repo_id}/nodes/search", response_model=list[NodeSearchResult])
async def search_nodes(
    repo_id: str,
    q: str = Query(..., description="Search query"),
    limit: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    _repo: object = Depends(with_repo),
) -> list[NodeSearchResult]:
    """Full-text search over node_id values."""
    result = await session.execute(
        select(GraphNode)
        .where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_id.ilike(f"%{_escape_like(q)}%"),
        )
        .order_by(GraphNode.symbol_count.desc(), GraphNode.pagerank.desc())
        .limit(limit)
    )
    nodes = result.scalars().all()
    return [
        NodeSearchResult(node_id=n.node_id, language=n.language, symbol_count=n.symbol_count)
        for n in nodes
    ]


@router.get("/{repo_id}", response_model=GraphExportResponse)
async def export_graph(
    repo_id: str,
    limit: int = Query(
        _FULL_GRAPH_NODE_CAP,
        ge=1,
        le=6000,
        description="Maximum nodes to return (top-N by PageRank). Stepped up by the client.",
    ),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    _repo: object = Depends(with_repo),
) -> GraphExportResponse:
    """Export the full dependency graph in D3 force-directed format.

    Large repos are capped to top-N nodes by PageRank with ``truncated=True``;
    clients should surface a banner and let the user request unrestricted load.
    """
    node_result = await session.execute(
        select(GraphNode)
        .where(GraphNode.repository_id == repo_id)
        .order_by(GraphNode.pagerank.desc())
    )
    all_nodes = node_result.scalars().all()
    total_node_count = len(all_nodes)
    truncated = total_node_count > limit
    nodes = all_nodes[:limit] if truncated else all_nodes
    kept_ids = {n.node_id for n in nodes}

    edge_result = await session.execute(select(GraphEdge).where(GraphEdge.repository_id == repo_id))
    edges = edge_result.scalars().all()

    signals = await _collect_node_signals(session, repo_id, list(kept_ids) if truncated else None)

    node_responses = [_to_graph_node(n, signals.get(n.node_id, _EMPTY_SIGNALS)) for n in nodes]

    link_responses = [
        _edge_response(e)
        for e in edges
        if e.source_node_id in kept_ids and e.target_node_id in kept_ids
    ]

    return GraphExportResponse(
        nodes=node_responses,
        links=link_responses,
        truncated=truncated,
        total_node_count=total_node_count,
    )
