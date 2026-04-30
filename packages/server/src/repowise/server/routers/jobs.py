"""/api/jobs — Generation job status and SSE progress stream."""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GenerationJob, LlmCost
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import JobResponse

router = APIRouter(
    prefix="/api/jobs",
    tags=["jobs"],
    dependencies=[Depends(verify_api_key)],
)


async def _find_job_factory(app_state, job_id: str):
    """Locate the session_factory whose database contains ``job_id``.

    In workspace mode each repo has its own ``wiki.db``. The /api/jobs/*
    endpoints don't take repo_id in the path, so we must scan: primary
    first, then all per-repo factories. Returns ``(factory, job)`` or
    ``(None, None)`` if no DB has the row.
    """
    candidates = [app_state.session_factory]
    ws = getattr(app_state, "workspace_sessions", None)
    if ws:
        candidates.extend(ws.values())
    seen: set[int] = set()
    for factory in candidates:
        if id(factory) in seen:
            continue
        seen.add(id(factory))
        try:
            async with get_session(factory) as session:
                job = await crud.get_generation_job(session, job_id)
            if job is not None:
                return factory, job
        except Exception:
            # Don't let one bad DB poison the lookup; try the rest.
            continue
    return None, None


@router.get("", response_model=list[JobResponse])
async def list_jobs(
    repo_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[JobResponse]:
    """List generation jobs, optionally filtered by repository or status."""
    q = select(GenerationJob)
    if repo_id:
        q = q.where(GenerationJob.repository_id == repo_id)
    if status:
        q = q.where(GenerationJob.status == status)
    q = q.order_by(GenerationJob.created_at.desc()).limit(limit).offset(offset)

    result = await session.execute(q)
    jobs = result.scalars().all()
    return [JobResponse.from_orm(j) for j in jobs]


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, request: Request) -> JobResponse:
    """Get a single generation job by ID."""
    _, job = await _find_job_factory(request.app.state, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResponse.from_orm(job)


@router.post("/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(job_id: str, request: Request) -> JobResponse:
    """Cancel a pending or running generation job.

    Marks the job as ``failed`` with a cancellation message. The background
    task itself is not interrupted — but in practice this unblocks the
    active-job guard in /repos/{id}/sync so the user can start a new sync
    immediately. Useful when a job is stuck in ``pending`` because the
    background task crashed before it could record a failure.
    """
    factory, job = await _find_job_factory(request.app.state, job_id)
    if job is None or factory is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("pending", "running"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel a job in '{job.status}' state",
        )
    async with get_session(factory) as session:
        await crud.update_job_status(
            session,
            job_id,
            "failed",
            error_message="Cancelled by user",
        )
        job = await crud.get_generation_job(session, job_id)
    assert job is not None  # we just updated it
    return JobResponse.from_orm(job)


@router.get("/{job_id}/stream")
async def stream_job(job_id: str, request: Request) -> StreamingResponse:
    """SSE progress stream for a generation job.

    Emits ``event: progress`` every second until the job completes or fails,
    then emits ``event: done`` and closes.
    """
    factory, _ = await _find_job_factory(request.app.state, job_id)
    if factory is None:
        # Fall back to the primary so we still emit a structured "not found"
        # event instead of a connection error.
        factory = request.app.state.session_factory

    async def event_generator():
        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                return

            async with get_session(factory) as session:
                job = await crud.get_generation_job(session, job_id)

            if job is None:
                data = json.dumps({"detail": "Job not found"})
                yield f"event: error\ndata: {data}\n\n"
                return

            # Sum LLM costs recorded since the job started
            actual_cost_usd: float | None = None
            if job.started_at is not None:
                cost_q = select(func.sum(LlmCost.cost_usd)).where(
                    LlmCost.repository_id == job.repository_id,
                    LlmCost.ts >= job.started_at,
                )
                async with get_session(factory) as cost_session:
                    actual_cost_usd = await cost_session.scalar(cost_q)

            progress = {
                "job_id": job.id,
                "status": job.status,
                "completed_pages": job.completed_pages,
                "total_pages": job.total_pages,
                "failed_pages": job.failed_pages,
                "current_level": job.current_level,
                "actual_cost_usd": actual_cost_usd,
            }
            data = json.dumps(progress)
            yield f"event: progress\ndata: {data}\n\n"

            if job.status in ("completed", "failed"):
                yield f"event: done\ndata: {data}\n\n"
                return

            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
