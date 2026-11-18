"""Shortest dependency-path lookup between two nodes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.exclusion import build_exclude_spec
from repowise.core.persistence.models import Repository
from repowise.server.deps import get_db_session
from repowise.server.routers.graph._common import with_repo
from repowise.server.schemas import DependencyPathResponse
from repowise.server.services.graph_views import load_code_graph

router = APIRouter()


@router.get(
    "/{repo_id}/path",
    response_model=DependencyPathResponse,
    # ``visual_context`` is only built when no path exists.
    response_model_exclude_unset=True,
)
async def dependency_path(
    repo_id: str,
    source: str = Query(..., alias="from", description="Source node ID"),
    target: str = Query(..., alias="to", description="Target node ID"),
    session: AsyncSession = Depends(get_db_session),
    repo: Repository = Depends(with_repo),
) -> dict:
    """Find the shortest dependency path between two nodes.

    When no direct path exists, returns visual context with nearest common
    ancestors, shared neighbors, and bridge suggestions.
    """
    try:
        import networkx as nx
    except ImportError:
        raise HTTPException(
            status_code=501, detail="networkx not available for path queries"
        ) from None

    # The graph the MCP twin (`get_dependency_path`) walks, exclude rules
    # included, so an excluded file is never a hop here either.
    code_graph = await load_code_graph(session, repo_id, build_exclude_spec(repo.local_path))
    graph = code_graph.graph

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
            "visual_context": _build_visual_context(
                graph, source, target, code_graph.nodes.values(), nx
            ),
        }

    return {
        "path": path,
        "distance": len(path) - 1,
        "explanation": f"Shortest path from {source} to {target} has {len(path) - 1} hops",
    }
