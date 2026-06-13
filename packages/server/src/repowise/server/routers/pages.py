"""/api/pages — Wiki page CRUD endpoints.

Note: Routes with path suffixes (/versions, /regenerate) must be defined
BEFORE the catch-all {page_id:path} route, otherwise FastAPI's path
parameter greedily matches the suffix as part of the page_id.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.core.persistence.models import _now_utc
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


class PageNotesUpdate(BaseModel):
    """PATCH body for /lookup/notes. ``None`` clears the note."""

    human_notes: str | None = None


@router.patch("/lookup/notes", response_model=PageResponse)
async def update_page_notes(
    body: PageNotesUpdate,
    page_id: str = Query(..., description="Page ID"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> PageResponse:
    """Set or clear the human-curated note pinned above a page's generated
    content. Notes survive regeneration, so this never touches versions."""
    page = await crud.get_page(session, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    note = (body.human_notes or "").strip()
    page.human_notes = note or None
    page.updated_at = _now_utc()
    await session.flush()
    return PageResponse.from_orm(page)


@router.post("/lookup/regenerate", status_code=202)
async def regenerate_page_by_query(
    page_id: str = Query(..., description="Page ID"),
    style: str | None = Query(
        None,
        description="Optional wiki style to regenerate this page in (per-page override).",
    ),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Force-regenerate a single wiki page (page_id as query param).

    An optional ``style`` overrides the repo's default style for this page only
    (D10). It is validated here and carried in the job config; the executor
    resolves it when regenerating.
    """
    page = await crud.get_page(session, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")

    job_config: dict = {"mode": "single_page", "page_id": page_id}
    if style is not None:
        from repowise.core.generation.styles import is_known_style, list_styles

        if not is_known_style(style):
            valid = ", ".join(s.name for s in list_styles())
            raise HTTPException(
                status_code=400,
                detail=f"Unknown style '{style}'. Valid styles: {valid}.",
            )
        job_config["style"] = style

    job = await crud.upsert_generation_job(
        session,
        repository_id=page.repository_id,
        status="pending",
        config=job_config,
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
