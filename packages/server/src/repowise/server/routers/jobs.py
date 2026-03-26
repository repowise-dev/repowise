"""/api/jobs — Generation job status and SSE progress stream."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GenerationJob
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import JobResponse

router = APIRouter(
    prefix="/api/jobs",
    tags=["jobs"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("", response_model=list[JobResponse])
async def list_jobs(
    repo_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
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
async def get_job(
    job_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> JobResponse:
    """Get a single generation job by ID."""
    job = await crud.get_generation_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResponse.from_orm(job)


@router.get("/{job_id}/stream")
async def stream_job(job_id: str, request: Request) -> StreamingResponse:
    """SSE progress stream for a generation job.

    Emits ``event: progress`` every second until the job completes or fails,
    then emits ``event: done`` and closes.
    """
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

            progress = {
                "job_id": job.id,
                "status": job.status,
                "completed_pages": job.completed_pages,
                "total_pages": job.total_pages,
                "failed_pages": job.failed_pages,
                "current_level": job.current_level,
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
