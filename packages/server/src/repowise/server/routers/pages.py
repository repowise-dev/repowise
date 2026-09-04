"""/api/pages — Wiki page CRUD endpoints.

Note: Routes with path suffixes (/versions, /regenerate) must be defined
BEFORE the catch-all {page_id:path} route, otherwise FastAPI's path
parameter greedily matches the suffix as part of the page_id.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.generation.page_redirects import (
    SupersededError,
    repo_wide_successor_type,
    resolve_superseded,
)
from repowise.core.persistence import crud
from repowise.core.persistence.models import _now_utc
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import (
    JobAcceptedResponse,
    PageResponse,
    PageSummaryResponse,
    PageVersionResponse,
)

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/api/pages",
    tags=["pages"],
    dependencies=[Depends(verify_api_key)],
)

# Names the id the reader actually asked for when they were moved. Without it a
# redirect is indistinguishable from having requested the successor directly,
# and a client cannot tell the reader their link is out of date.
REDIRECTED_FROM_HEADER = "X-Repowise-Redirected-From"


async def _sole_page_of_type(session: AsyncSession, page_type: str, retired_id: str):
    """The store's single page of *page_type*, or ``None``.

    Some retirements hand off to "the repository's overview" rather than to a
    named id, because the retired id carries nothing that identifies the
    successor. The store is what knows, so it is asked here.

    Requires exactly one match. Zero means the index never produced the
    successor; more than one means the store holds several repositories and
    picking either would send readers of one repository into another's wiki.
    Both are refusals rather than guesses, and both are logged, because either
    strands every inbound link to the retired page.
    """
    from sqlalchemy import select

    from repowise.core.persistence.models import Page

    rows = (await session.execute(select(Page).where(Page.page_type == page_type))).scalars().all()
    if len(rows) == 1:
        return rows[0]
    logger.warning(
        "page_redirect_repo_wide_unresolved",
        page_id=retired_id,
        successor_type=page_type,
        matches=len(rows),
    )
    return None


async def _get_page_or_successor(session: AsyncSession, page_id: str, response: Response):
    """The requested page, or the page that took over from it.

    Wiki pages are public and linkable, so an id that stops being generated has
    to keep resolving. A miss is retried against the retirement table before it
    becomes a 404.

    Returns ``None`` when neither the page nor a successor exists — callers
    raise the 404, so a genuine miss can never be turned into a success here.
    """
    page = await crud.get_page(session, page_id)
    if page is not None:
        return page

    try:
        successor_id = resolve_superseded(page_id)
        repo_wide_type = None if successor_id else repo_wide_successor_type(page_id)
    except SupersededError:
        # The table is static and a test resolves every entry in it, so this
        # means a real bug. It must not take page serving down with it.
        logger.warning("page_redirect_table_broken", page_id=page_id, exc_info=True)
        return None

    if repo_wide_type is not None:
        successor = await _sole_page_of_type(session, repo_wide_type, page_id)
        if successor is None:
            return None
        logger.info("page_redirect_served", page_id=page_id, successor_id=successor.id)
        response.headers[REDIRECTED_FROM_HEADER] = page_id
        return successor

    if successor_id is None:
        return None

    successor = await crud.get_page(session, successor_id)
    if successor is None:
        # The table points at a page this index did not produce. Every inbound
        # link to the retired id is stranded, so say so rather than 404ing mute.
        logger.warning(
            "page_redirect_successor_missing",
            page_id=page_id,
            successor_id=successor_id,
        )
        return None

    logger.info("page_redirect_served", page_id=page_id, successor_id=successor_id)
    response.headers[REDIRECTED_FROM_HEADER] = page_id
    return successor


@router.get("", response_model=None)
async def list_pages(
    repo_id: str = Query(..., description="Repository ID"),
    page_type: str | None = Query(None, description="Filter by page type"),
    has_prose: bool | None = Query(
        None,
        description="Scoped to the model-written page types (the concept tree and "
        "onboarding): true = only the ones a model has written, false = only the "
        "ones still stubs; omit for every page of every type.",
    ),
    sort_by: str = Query(
        "updated_at", description="Sort field: updated_at, confidence, created_at"
    ),
    order: str = Query("desc", description="Sort order: asc or desc"),
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    fields: str = Query(
        "full",
        description="How much of each page to return. 'full' (the default) is "
        "every field. 'summary' drops 'content' and 'metadata' — together 95% "
        "of a listing's bytes, and read by nothing that renders a list of "
        "pages — and adds 'content_chars' in their place.",
    ),
    session: AsyncSession = Depends(get_db_session),
) -> list[PageResponse] | list[PageSummaryResponse]:
    """List wiki pages for a repository."""
    if fields not in ("full", "summary"):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown fields '{fields}'. Valid: full, summary.",
        )
    pages = await crud.list_pages(
        session,
        repo_id,
        page_type=page_type,
        has_prose=has_prose,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        order=order,
    )
    # response_model is off on this route: the two shapes are serialized by the
    # models themselves, so FastAPI can't coerce a summary back into a full row.
    if fields == "summary":
        return [PageSummaryResponse.from_orm(p) for p in pages]
    return [PageResponse.from_orm(p) for p in pages]


@router.get("/lookup", response_model=PageResponse)
async def get_page_by_query(
    response: Response,
    page_id: str = Query(..., description="Page ID (e.g. file_page:src/main.py)"),
    repo_id: str | None = Query(
        None,
        description="Repository the page belongs to. Optional, but required to "
        "reach a page outside the default store in workspace mode, where the "
        "session is routed by this value.",
    ),
    session: AsyncSession = Depends(get_db_session),
) -> PageResponse:
    """Get a single wiki page by ID passed as query parameter.

    Use this endpoint when the page_id contains characters that are
    difficult to encode in a URL path.

    ``repo_id`` is what routes the session to the right database. Without it a
    workspace server looks the page up in whichever store is the default, and a
    page id that exists in another repo comes back as a 404 — so any caller
    that knows the repo should say so.

    A retired page id resolves to whatever took over from it; the response then
    carries ``X-Repowise-Redirected-From``.
    """
    page = await _get_page_or_successor(session, page_id, response)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return PageResponse.from_orm(page)


@router.get("/lookup/versions", response_model=list[PageVersionResponse])
async def get_page_versions_by_query(
    page_id: str = Query(..., description="Page ID"),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
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
    session: AsyncSession = Depends(get_db_session),
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


@router.post("/lookup/regenerate", response_model=JobAcceptedResponse, status_code=202)
async def regenerate_page_by_query(
    request: Request,
    page_id: str = Query(..., description="Page ID"),
    style: str | None = Query(
        None,
        description="Optional wiki style to regenerate this page in (per-page override).",
    ),
    cascade: str = Query(
        "none",
        description="What to do with the pages that summarize this one: none / dependents / full.",
    ),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Force-regenerate a single wiki page (page_id as query param).

    Creates a ``single_page`` generation job and launches it immediately (D1):
    the previous version created a ``pending`` row and never committed or
    launched it, so the click did nothing until a 15-minute polling fallback
    picked it up while the active-job guard blocked syncs.

    An optional ``style`` overrides the repo's default style for this page only,
    and ``cascade`` controls whether the pages summarizing this one are refreshed
    too. Both are validated here and carried in the job config; the executor
    resolves them.
    """
    from repowise.server.routers.repos import _accepted, _ensure_no_active_job, _launch_job_task

    page = await crud.get_page(session, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")

    if cascade not in ("none", "dependents", "full"):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown cascade '{cascade}'. Valid: none, dependents, full.",
        )

    job_config: dict = {"mode": "single_page", "page_id": page_id, "cascade": cascade}
    if style is not None:
        from repowise.core.generation.styles import is_known_style, list_styles

        if not is_known_style(style):
            valid = ", ".join(s.name for s in list_styles())
            raise HTTPException(
                status_code=400,
                detail=f"Unknown style '{style}'. Valid styles: {valid}.",
            )
        job_config["style"] = style

    await _ensure_no_active_job(session, page.repository_id)

    job = await crud.upsert_generation_job(
        session,
        repository_id=page.repository_id,
        status="pending",
        config=job_config,
    )
    # Commit (not just flush) so the background task's separate session sees the
    # job row, then launch it — the fix for the click that did nothing.
    await session.commit()
    _launch_job_task(request, job.id, page.repository_id)
    return _accepted(job.id)


@router.get("/{page_id:path}", response_model=PageResponse)
async def get_page(
    page_id: str,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> PageResponse:
    """Get a single wiki page by ID in path (e.g. ``file_page:src/main.py``).

    The page_id is URL-decoded automatically by FastAPI.

    A retired page id resolves to whatever took over from it; the response then
    carries ``X-Repowise-Redirected-From``.
    """
    page = await _get_page_or_successor(session, page_id, response)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return PageResponse.from_orm(page)
