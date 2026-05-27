"""Shortest dependency-path lookup between two nodes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import GraphEdge, GraphNode
from repowise.server.deps import get_db_session
from repowise.server.routers.graph._common import with_repo

router = APIRouter()


@router.get("/{repo_id}/path")
async def dependency_path(
    repo_id: str,
    source: str = Query(..., alias="from", description="Source node ID"),
    target: str = Query(..., alias="to", description="Target node ID"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    _repo: object = Depends(with_repo),
) -> dict:
    """Find the shortest dependency path between two nodes.

    When no direct path exists, returns visual context with nearest common
    ancestors, shared neighbors, and bridge suggestions.
    """
    edge_result = await session.execute(select(GraphEdge).where(GraphEdge.repository_id == repo_id))
    edges = edge_result.scalars().all()

    node_result = await session.execute(select(GraphNode).where(GraphNode.repository_id == repo_id))
    nodes = node_result.scalars().all()

    try:
        import networkx as nx
    except ImportError:
        raise HTTPException(
            status_code=501, detail="networkx not available for path queries"
        ) from None

    graph: nx.DiGraph = nx.DiGraph()
    for e in edges:
        graph.add_edge(e.source_node_id, e.target_node_id)

    if source not in graph:
        raise HTTPException(status_code=404, detail=f"Source node '{source}' not found in graph")
    if target not in graph:
        raise HTTPException(status_code=404, detail=f"Target node '{target}' not found in graph")

    try:
        path = nx.shortest_path(graph, source, target)
    except nx.NetworkXNoPath:
        from repowise.server.mcp_server import _build_visual_context

        return {
            "path": [],
            "distance": -1,
            "explanation": "No direct dependency path found",
            "visual_context": _build_visual_context(graph, source, target, nodes, nx),
        }

    return {
        "path": path,
        "distance": len(path) - 1,
        "explanation": f"Shortest path from {source} to {target} has {len(path) - 1} hops",
    }
