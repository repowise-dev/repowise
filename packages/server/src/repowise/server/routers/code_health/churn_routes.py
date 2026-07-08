"""Churn x complexity scatter route."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.churn_complexity import churn_complexity_points
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session

from ._router import router
from .serializers import _churn_complexity_to_dict


@router.get("/api/repos/{repo_id}/health/churn-complexity")
async def churn_complexity(
    repo_id: str,
    limit: int = Query(300, ge=1, le=1000),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Churn x complexity scatter points -- the "hotspot anatomy" danger-zone view.

    One point per recently-changed file: x = 90-day commit count (churn),
    y = max cyclomatic complexity, dot size = NLOC, color = health band. The
    top-right corner is where churn and complexity collide -- the highest-value
    refactoring targets, plotted instead of listed.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    metrics = await crud.get_health_metrics(session, repo_id)
    git_meta = await crud.get_all_git_metadata(session, repo_id)
    points = churn_complexity_points(metrics, git_meta)
    return {
        "points": [_churn_complexity_to_dict(p) for p in points[:limit]],
        "total": len(points),
    }
