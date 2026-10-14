"""/api/repos/{repo_id}/changes — merged changes reconstructed from git."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.attribution import attribution_enabled
from repowise.core.changes.record import change_record
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas.changes import (
    ChangeListResponse,
    ChangeRecord,
    summary_from_record,
)

router = APIRouter(
    prefix="/api/repos",
    tags=["changes"],
    dependencies=[Depends(verify_api_key)],
)

_MAX_PAGE = 200


async def _attribution(session: AsyncSession, repo_id: str) -> bool:
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return attribution_enabled(repo.local_path)


@router.get("/{repo_id}/changes", response_model=ChangeListResponse)
async def list_changes(
    repo_id: str,
    category: str | None = Query(None, description="One commit category, e.g. fix"),
    shape: str | None = Query(None, description="merge | squash | commit"),
    q: str | None = Query(None, description="Substring of the title, id or a file path"),
    limit: int = Query(50, ge=1, le=_MAX_PAGE),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> ChangeListResponse:
    """A page of changes, newest merged first."""
    attribution = await _attribution(session, repo_id)
    rows, total = await crud.list_changes(
        session, repo_id, category=category, shape=shape, q=q, limit=limit, offset=offset
    )
    return ChangeListResponse(
        items=[summary_from_record(change_record(r, attribution=attribution)) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
        attribution=attribution,
    )


@router.get("/{repo_id}/changes/{change_id}", response_model=ChangeRecord)
async def get_change(
    repo_id: str,
    change_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> ChangeRecord:
    """One change by id (``pr-123``), or the change that landed a commit sha."""
    attribution = await _attribution(session, repo_id)
    row = await crud.get_change(session, repo_id, change_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Change not found")
    record = change_record(row, attribution=attribution)
    return ChangeRecord(**record, commit_count=len(record["commits"]))
