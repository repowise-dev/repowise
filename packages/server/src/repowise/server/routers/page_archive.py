"""Repository-bound retired-page readers; live retrieval is unchanged."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.server.deps import get_db_session
from repowise.server.schemas.page_archive import RetiredPageDetail, RetiredPageSummary

router = APIRouter(prefix="/{repo_id}/pages/retired", tags=["pages"])


@router.get("", response_model=list[RetiredPageSummary])
async def list_retired_pages(
    repo_id: str,
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """List retired identities without returning full bodies in a listing."""
    if await crud.get_repository(session, repo_id) is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    pages = await crud.list_retired_pages(session, repo_id, limit=limit, offset=offset)
    return [crud.retired_page_summary(page) for page in pages]


@router.get("/{page_id:path}", response_model=RetiredPageDetail)
async def get_retired_page(
    repo_id: str,
    page_id: str,
    archive_id: str | None = Query(None),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Read an old page and all its versions, optionally selecting an exact retirement."""
    page = await crud.get_retired_page(session, repo_id, page_id, archive_id=archive_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Retired page not found")
    versions = await crud.get_retired_page_versions(session, repo_id, page.id)
    return crud.retired_page_detail(page, versions)
