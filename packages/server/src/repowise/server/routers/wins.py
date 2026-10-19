"""/api/repos/{repo_id}/wins — what got better in a window."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.routers._history_filters import utc
from repowise.server.schemas.history import WinsResponse
from repowise.server.services.history.wins import build_wins

router = APIRouter(
    prefix="/api/repos",
    tags=["history"],
    dependencies=[Depends(verify_api_key)],
)

DEFAULT_WINDOW_DAYS = 30


@router.get("/{repo_id}/wins", response_model=WinsResponse)
async def get_wins(
    repo_id: str,
    since: datetime | None = Query(None, description="ISO 8601; default 30 days ago"),
    until: datetime | None = Query(None, description="ISO 8601, exclusive; default now"),
    author: str | None = Query(
        None, description="Author email (any case) or exact name: that person's wins only"
    ),
    top: int = Query(10, ge=1, le=50, description="Items per section"),
    session: AsyncSession = Depends(get_db_session),
) -> WinsResponse:
    """Findings resolved, files whose score rose and refactorings that landed.

    Positive only: an author gets credit, never a comparison.
    """
    if await crud.get_repository(session, repo_id) is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    since = utc(since) or datetime.now(UTC) - timedelta(days=DEFAULT_WINDOW_DAYS)
    return await build_wins(
        session, repo_id, since=since, until=utc(until), author=author, top=top
    )
