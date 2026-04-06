"""/api/repos — Repository CRUD + sync endpoints."""

from __future__ import annotations

import asyncio
import io
import logging
import zipfile
from pathlib import PurePosixPath

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from repowise.core.persistence import crud
from repowise.core.persistence.models import (
    DeadCodeFinding,
    GenerationJob,
    GraphNode,
    Page,
    Repository,
)
from repowise.server.deps import get_db_session, get_fts, verify_api_key
from repowise.server.job_executor import execute_job
from repowise.server.schemas import RepoCreate, RepoResponse, RepoStatsResponse, RepoUpdate

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/repos",
    tags=["repos"],
    dependencies=[Depends(verify_api_key)],
)


@router.post("", response_model=RepoResponse, status_code=201)
async def create_repo(
    body: RepoCreate,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> RepoResponse:
    """Register a new repository (or update if same local_path exists)."""
    repo = await crud.upsert_repository(
        session,
        name=body.name,
        local_path=body.local_path,
        url=body.url,
        default_branch=body.default_branch,
        settings=body.settings,
    )
    return RepoResponse.from_orm(repo)


@router.get("", response_model=list[RepoResponse])
async def list_repos(
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[RepoResponse]:
    """List all registered repositories.

    In workspace mode, aggregates repos from the primary DB and all
    workspace repo DBs so every indexed repo appears in the sidebar.
    """
    result = await session.execute(select(Repository).order_by(Repository.updated_at.desc()))
    repos = list(result.scalars().all())
    seen_ids = {r.id for r in repos}

    # In workspace mode, also fetch repos from other workspace DBs
    ws_sessions: dict = getattr(request.app.state, "workspace_sessions", {})
    for repo_id, ws_factory in ws_sessions.items():
        if repo_id in seen_ids:
            continue
        try:
            async with ws_factory() as ws_session:
                ws_result = await ws_session.execute(
                    select(Repository).where(Repository.id == repo_id)
                )
                ws_repo = ws_result.scalar_one_or_none()
                if ws_repo:
                    repos.append(ws_repo)
                    seen_ids.add(ws_repo.id)
        except Exception:
            pass

    # Sort by updated_at descending
    repos.sort(key=lambda r: r.updated_at or r.created_at, reverse=True)
    return [RepoResponse.from_orm(r) for r in repos]


@router.get("/{repo_id}", response_model=RepoResponse)
async def get_repo(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> RepoResponse:
    """Get a single repository by ID."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return RepoResponse.from_orm(repo)


@router.patch("/{repo_id}", response_model=RepoResponse)
async def update_repo(
    repo_id: str,
    body: RepoUpdate,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> RepoResponse:
    """Update repository fields."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    if body.name is not None:
        repo.name = body.name
    if body.url is not None:
        repo.url = body.url
    if body.default_branch is not None:
        repo.default_branch = body.default_branch
    if body.settings is not None:
        import json

        repo.settings_json = json.dumps(body.settings)
    await session.flush()
    return RepoResponse.from_orm(repo)


@router.delete("/{repo_id}")
async def delete_repo(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    fts=Depends(get_fts),  # noqa: B008
) -> dict:
    """Delete a repository and all its data."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    # Collect page IDs before CASCADE deletes the Page rows
    page_ids = await crud.list_page_ids(session, repo_id)

    # Clean up FTS index (FTS5 virtual table has no FK cascade).
    # fts is always initialized in the lifespan before the server accepts
    # requests, so this guard is purely defensive.
    if fts is not None:
        await fts.delete_many(page_ids)

    # Delete repository — CASCADE handles all child ORM tables
    await crud.delete_repository(session, repo_id)

    return {"ok": True, "deleted_pages": len(page_ids)}


@router.get("/{repo_id}/stats", response_model=RepoStatsResponse)
async def get_repo_stats(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> RepoStatsResponse:
    """Get aggregate stats for a repository."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    file_count_result = await session.execute(
        select(func.count(GraphNode.id)).where(GraphNode.repository_id == repo_id)
    )
    file_count = file_count_result.scalar_one() or 0

    symbol_count_result = await session.execute(
        select(func.sum(GraphNode.symbol_count)).where(GraphNode.repository_id == repo_id)
    )
    symbol_count = int(symbol_count_result.scalar_one() or 0)

    entry_count_result = await session.execute(
        select(func.count(GraphNode.id)).where(
            GraphNode.repository_id == repo_id,
            GraphNode.is_entry_point == True,  # noqa: E712
        )
    )
    entry_point_count = entry_count_result.scalar_one() or 0

    avg_conf_result = await session.execute(
        select(func.avg(Page.confidence)).where(Page.repository_id == repo_id)
    )
    avg_confidence = float(avg_conf_result.scalar_one() or 0.0)
    doc_coverage_pct = avg_confidence * 100

    dead_result = await session.execute(
        select(func.count(DeadCodeFinding.id)).where(
            DeadCodeFinding.repository_id == repo_id,
            DeadCodeFinding.kind == "unused_export",
            DeadCodeFinding.status == "open",
        )
    )
    dead_export_count = dead_result.scalar_one() or 0

    # Compute true freshness score from actual page freshness statuses
    total_pages_result = await session.execute(
        select(func.count(Page.id)).where(Page.repository_id == repo_id)
    )
    total_pages = total_pages_result.scalar_one() or 0

    fresh_pages_result = await session.execute(
        select(func.count(Page.id)).where(
            Page.repository_id == repo_id,
            Page.freshness_status == "fresh",
        )
    )
    fresh_pages = fresh_pages_result.scalar_one() or 0

    freshness_score = (fresh_pages / total_pages * 100) if total_pages > 0 else doc_coverage_pct

    return RepoStatsResponse(
        file_count=file_count,
        symbol_count=symbol_count,
        entry_point_count=entry_point_count,
        doc_coverage_pct=doc_coverage_pct,
        freshness_score=freshness_score,
        dead_export_count=dead_export_count,
    )


@router.post("/{repo_id}/sync", status_code=202)
async def sync_repo(
    repo_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Trigger an incremental documentation sync for a repository.

    Creates a generation job, launches the pipeline in the background,
    and returns immediately with the job ID.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    # Prevent concurrent pipeline runs on the same repo
    active = await session.execute(
        select(GenerationJob.id)
        .where(GenerationJob.repository_id == repo_id)
        .where(GenerationJob.status.in_(["pending", "running"]))
        .limit(1)
    )
    if active.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="A sync job is already in progress for this repository")

    job = await crud.upsert_generation_job(
        session,
        repository_id=repo_id,
        status="pending",
    )
    # Commit (not just flush) so the background task's separate session can
    # see the job row.  SQLite WAL isolation hides uncommitted rows from
    # other connections, so flush() alone is not sufficient.
    await session.commit()
    _launch_job_task(request, job.id)
    return {"job_id": job.id, "status": "accepted"}


@router.post("/{repo_id}/full-resync", status_code=202)
async def full_resync(
    repo_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Trigger a full re-generation of all documentation.

    Creates a generation job, launches the pipeline in the background,
    and returns immediately with the job ID.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    # Prevent concurrent pipeline runs on the same repo
    active = await session.execute(
        select(GenerationJob.id)
        .where(GenerationJob.repository_id == repo_id)
        .where(GenerationJob.status.in_(["pending", "running"]))
        .limit(1)
    )
    if active.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="A sync job is already in progress for this repository")

    job = await crud.upsert_generation_job(
        session,
        repository_id=repo_id,
        status="pending",
        config={"mode": "full_resync"},
    )
    # Commit (not just flush) so the background task's separate session can
    # see the job row.  See sync_repo comment for rationale.
    await session.commit()
    _launch_job_task(request, job.id)
    return {"job_id": job.id, "status": "accepted"}


def _launch_job_task(request: Request, job_id: str) -> None:
    """Launch a background job task with proper lifecycle management.

    Stores a strong reference in ``app.state.background_tasks`` to prevent
    garbage collection, and removes it when the task finishes.  Exceptions
    are logged instead of silently swallowed.
    """
    task = asyncio.create_task(execute_job(job_id, request.app.state), name=f"job-{job_id}")
    bg_tasks: set[asyncio.Task] = request.app.state.background_tasks  # type: ignore[assignment]
    bg_tasks.add(task)

    def _on_done(t: asyncio.Task) -> None:
        bg_tasks.discard(t)
        if not t.cancelled() and t.exception() is not None:
            logger.error("background_job_failed", exc_info=t.exception())

    task.add_done_callback(_on_done)


@router.get("/{repo_id}/export")
async def export_wiki(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> StreamingResponse:
    """Export all wiki pages as a ZIP of markdown files with folder structure."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    pages = (await session.execute(select(Page).where(Page.repository_id == repo_id))).scalars().all()
    if not pages:
        raise HTTPException(status_code=404, detail="No pages to export")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for page in pages:
            target = page.target_path or page.id
            safe = (
                target.replace("::", "/")
                .replace("->", "--")
                .replace("\\", "/")
            )
            path = PurePosixPath("wiki") / page.page_type / safe
            if path.suffix != ".md":
                path = path.with_suffix(path.suffix + ".md")

            content = f"# {page.title}\n\n{page.content}"
            zf.writestr(str(path), content)

    buf.seek(0)
    filename = f"{repo.name}-wiki.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
