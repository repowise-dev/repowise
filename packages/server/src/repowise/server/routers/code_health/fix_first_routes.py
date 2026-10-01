"""The Fix-first queue: one ranked list of what to fix, as core builds it.

A thin adapter over ``load_fix_first``; ranking, eligibility and copy are
core's, so this surface and the agent surface cannot disagree.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.fix_first import FIX_SCOPES, FixItem
from repowise.core.analysis.health.fix_first.model import FixTotals
from repowise.core.persistence.crud.analysis.fix_first import load_fix_first
from repowise.server.deps import get_db_session

from ._router import router


class FixFirstQueueResponse(BaseModel):
    """``FixFirstQueue.as_dict()``: the stored fields plus the ``lead`` it derives."""

    items: list[FixItem]
    lead: FixItem | None
    totals: FixTotals
    by_improves: dict[str, int]
    model_version: int
    basis: dict[str, str | None]


def _scope(scope: str) -> str:
    if scope not in FIX_SCOPES:
        raise HTTPException(status_code=422, detail=f"scope must be one of {list(FIX_SCOPES)}")
    return scope


@router.get("/api/repos/{repo_id}/health/fix-first", response_model=FixFirstQueueResponse)
async def get_fix_first(
    repo_id: str,
    limit: int = Query(10, ge=0, le=50),
    scope: str = Query("production", description="production (default) or all"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """The ranked queue, its lead, and how many units each rule excluded."""
    queue = await load_fix_first(session, repo_id, limit=limit, scope=_scope(scope))
    return queue.as_dict()


@router.get("/api/repos/{repo_id}/health/fix-first/{fix_id}", response_model=FixItem)
async def get_fix_first_item(
    repo_id: str,
    fix_id: str,
    scope: str = Query("production"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """One item by its stable id, wherever it ranks."""
    queue = await load_fix_first(session, repo_id, scope=_scope(scope), item_id=fix_id)
    item = queue.find(fix_id)
    if item is None:
        raise HTTPException(status_code=404, detail="No open Fix-first item with that id")
    return item.as_dict()


__all__ = ["get_fix_first", "get_fix_first_item"]
