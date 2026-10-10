"""The Fix-first queue: one ranked list of what to fix, as core builds it.

A thin adapter over ``load_fix_first``; ranking, eligibility and copy are
core's, so this surface and the agent surface cannot disagree.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.agent_prompts import Flavor, render_fix_item
from repowise.core.analysis.health.fix_first import FIX_SCOPES, FixItem
from repowise.core.analysis.health.fix_first.model import FixTotals
from repowise.core.analysis.health.queue.counts import QueueCounts
from repowise.core.persistence import crud
from repowise.core.persistence.crud.analysis.fix_first import load_fix_first, queue_view
from repowise.core.persistence.models import Repository
from repowise.server.deps import get_db_session
from repowise.server.schemas.agent_prompts import AgentPromptResponse

from ._router import router


class FixFirstQueueResponse(BaseModel):
    """``FixFirstQueue.as_dict()``: the stored fields plus the ``lead`` it derives."""

    items: list[FixItem]
    lead: FixItem | None
    totals: FixTotals
    by_improves: dict[str, int]
    model_version: int
    basis: dict[str, str | None]
    #: The items' count vocabulary; ``totals`` stays for clients that read it.
    counts: QueueCounts


def _scope(scope: str) -> str:
    if scope not in FIX_SCOPES:
        raise HTTPException(status_code=422, detail=f"scope must be one of {list(FIX_SCOPES)}")
    return scope


async def _item(
    session: AsyncSession, repo_id: str, fix_id: str, scope: str
) -> tuple[Repository, FixItem]:
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    queue = await load_fix_first(session, repo_id, scope=_scope(scope), item_id=fix_id)
    item = queue.find(fix_id)
    if item is None:
        raise HTTPException(status_code=404, detail="No open Fix-first item with that id")
    return repo, item


@router.get("/api/repos/{repo_id}/health/fix-first", response_model=FixFirstQueueResponse)
async def get_fix_first(
    repo_id: str,
    limit: int = Query(10, ge=0, le=50),
    scope: str = Query("production", description="production (default) or all"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """The ranked queue, its lead, and how many units each rule excluded."""
    full = await load_fix_first(session, repo_id, limit=None, scope=_scope(scope))
    queue = queue_view(full, limit=limit)
    return {**queue.as_dict(), "counts": full.counts(len(queue.items)).as_dict()}


@router.get("/api/repos/{repo_id}/health/fix-first/{fix_id}", response_model=FixItem)
async def get_fix_first_item(
    repo_id: str,
    fix_id: str,
    scope: str = Query("production"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """One item by its stable id, wherever it ranks."""
    _, item = await _item(session, repo_id, fix_id, scope)
    return item.as_dict()


@router.get(
    "/api/repos/{repo_id}/health/fix-first/{fix_id}/prompt",
    response_model=AgentPromptResponse,
)
async def get_fix_first_item_prompt(
    repo_id: str,
    fix_id: str,
    flavor: Flavor = Query("generic"),
    scope: str = Query("production"),
    session: AsyncSession = Depends(get_db_session),
) -> AgentPromptResponse:
    """One item as the prompt an agent starts from, worded for its harness."""
    repo, item = await _item(session, repo_id, fix_id, scope)
    return AgentPromptResponse(flavor=flavor, text=render_fix_item(item.as_dict(), flavor, repo.name))


__all__ = ["get_fix_first", "get_fix_first_item", "get_fix_first_item_prompt"]
