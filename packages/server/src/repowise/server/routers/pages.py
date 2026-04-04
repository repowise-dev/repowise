"""/api/pages — Wiki page CRUD endpoints.

Note: Routes with path suffixes (/versions, /regenerate) must be defined
BEFORE the catch-all {page_id:path} route, otherwise FastAPI's path
parameter greedily matches the suffix as part of the page_id.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, Query
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import PageResponse, PageVersionResponse

router = APIRouter(
    prefix="/api/pages",
    tags=["pages"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("", response_model=list[PageResponse])
async def list_pages(
    repo_id: str = Query(..., description="Repository ID"),
    page_type: str | None = Query(None, description="Filter by page type"),
    sort_by: str = Query(
        "updated_at", description="Sort field: updated_at, confidence, created_at"
    ),
    order: str = Query("desc", description="Sort order: asc or desc"),
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[PageResponse]:
    """List wiki pages for a repository."""
    pages = await crud.list_pages(
        session,
        repo_id,
        page_type=page_type,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        order=order,
    )
    return [PageResponse.from_orm(p) for p in pages]


@router.get("/lookup", response_model=PageResponse)
async def get_page_by_query(
    page_id: str = Query(..., description="Page ID (e.g. file_page:src/main.py)"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> PageResponse:
    """Get a single wiki page by ID passed as query parameter.

    Use this endpoint when the page_id contains characters that are
    difficult to encode in a URL path.
    """
    page = await crud.get_page(session, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return PageResponse.from_orm(page)


@router.get("/lookup/versions", response_model=list[PageVersionResponse])
async def get_page_versions_by_query(
    page_id: str = Query(..., description="Page ID"),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[PageVersionResponse]:
    """Get version history for a wiki page (page_id as query param)."""
    versions = await crud.get_page_versions(session, page_id, limit=limit)
    return [PageVersionResponse.from_orm(v) for v in versions]


@router.post("/lookup/regenerate", status_code=202)
async def regenerate_page_by_query(
    page_id: str = Query(..., description="Page ID"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Force-regenerate a single wiki page (page_id as query param)."""
    page = await crud.get_page(session, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")

    job = await crud.upsert_generation_job(
        session,
        repository_id=page.repository_id,
        status="pending",
        config={"mode": "single_page", "page_id": page_id},
    )
    return {"job_id": job.id, "status": "accepted"}


@router.get("/{page_id:path}", response_model=PageResponse)
async def get_page(
    page_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> PageResponse:
    """Get a single wiki page by ID in path (e.g. ``file_page:src/main.py``).

    The page_id is URL-decoded automatically by FastAPI.
    """
    page = await crud.get_page(session, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return PageResponse.from_orm(page)
