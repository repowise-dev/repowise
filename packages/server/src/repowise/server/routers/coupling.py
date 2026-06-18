"""/api/repos/{repo_id}/coupling — repo-wide change-coupling graph.

Surfaces the per-file co-change partners the git indexer already persists
(``GitMetadata.co_change_partners_json``) as a single deduplicated, undirected
edge list, enriched with each node's module / health score / size so the UI can
group, color, and size the graph. Pure surfacing: no recompute, no LLM. The
join mirrors ``code_health.py``'s churn-complexity endpoint.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.coupling import (
    CouplingEdge,
    CouplingNode,
    coupling_graph,
)
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session, verify_api_key

router = APIRouter(
    tags=["coupling"],
    dependencies=[Depends(verify_api_key)],
)


def _node_to_dict(n: CouplingNode) -> dict[str, Any]:
    return {
        "file_path": n.file_path,
        "module": n.module,
        "score": n.score,
        "nloc": n.nloc,
    }


def _edge_to_dict(e: CouplingEdge) -> dict[str, Any]:
    return {
        "source": e.source,
        "target": e.target,
        "strength": e.strength,
        "last_co_change": e.last_co_change,
    }


@router.get("/api/repos/{repo_id}/coupling")
async def coupling(
    repo_id: str,
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Return the repo's change-coupling graph (strongest *limit* edges).

    Co-change is a temporal hint (files committed together), not a verified
    dependency; ``strength`` is the decay-weighted count, not a percentage.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    metrics = await crud.get_health_metrics(session, repo_id)
    git_meta = await crud.get_all_git_metadata(session, repo_id)
    graph = coupling_graph(metrics, git_meta, limit=limit)
    return {
        "nodes": [_node_to_dict(n) for n in graph.nodes],
        "edges": [_edge_to_dict(e) for e in graph.edges],
        "total_edges": graph.total_edges,
    }
