"""/api/repos — Repository CRUD + sync endpoints."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, Request
from repowise.core.persistence import crud
from repowise.core.persistence.models import (
    DeadCodeFinding,
    GenerationJob,
    GraphNode,
    Page,
    Repository,
)
from repowise.server.deps import get_db_session, verify_api_key
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
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[RepoResponse]:
    """List all registered repositories."""
    result = await session.execute(select(Repository).order_by(Repository.updated_at.desc()))
    repos = result.scalars().all()
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

    return RepoStatsResponse(
        file_count=file_count,
        symbol_count=symbol_count,
        entry_point_count=entry_point_count,
        doc_coverage_pct=doc_coverage_pct,
        freshness_score=doc_coverage_pct,
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
