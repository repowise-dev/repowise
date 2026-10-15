"""/api/repos/{repo_id}/events — typed events from consecutive index runs (pull)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.events.model import KINDS, to_cloudevent
from repowise.core.events.store import empty_events_reason, list_events, snapshot_run_count
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas.events import CloudEvent, EventListResponse

router = APIRouter(
    prefix="/api/repos",
    tags=["events"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("/{repo_id}/events", response_model=EventListResponse)
async def get_events(
    repo_id: str,
    since: datetime | None = Query(None, description="Only events at or after this time"),
    kind: list[str] = Query(default_factory=list, description="Only these kinds"),
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_db_session),
) -> EventListResponse:
    """Stored events, oldest first, as CloudEvents."""
    if await crud.get_repository(session, repo_id) is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    unknown = [k for k in kind if k not in KINDS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown event kind(s): {unknown}")
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    events = await list_events(session, repo_id, since=since, kinds=kind, limit=limit)
    runs = await snapshot_run_count(session, repo_id)
    reason = None if events else empty_events_reason(runs, filtered=bool(since or kind))
    return EventListResponse(
        events=[CloudEvent(**to_cloudevent(e)) for e in events],
        snapshot_runs=runs,
        empty_reason=reason[0] if reason else None,
        empty_note=reason[1] if reason else None,
    )
