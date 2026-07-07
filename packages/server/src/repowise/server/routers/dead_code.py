"""/api/repos/{repo_id}/dead-code — Dead code findings endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.dead_code.risk_factors import effective_safe_to_delete
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import (
    DeadCodeFindingResponse,
    DeadCodePatchRequest,
    DeadCodeSummaryResponse,
)

router = APIRouter(
    tags=["dead-code"],
    dependencies=[Depends(verify_api_key)],
)


@router.get(
    "/api/repos/{repo_id}/dead-code",
    response_model=list[DeadCodeFindingResponse],
)
async def list_dead_code(
    repo_id: str,
    kind: str | None = Query(None, description="Filter by finding kind"),
    min_confidence: float = Query(0.4, ge=0.0, le=1.0),
    status: str = Query("open"),
    safe_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[DeadCodeFindingResponse]:
    """List dead code findings for a repository."""
    findings = await crud.get_dead_code_findings(
        session,
        repo_id,
        kind=kind,
        min_confidence=min_confidence,
        status=status,
    )
    if safe_only:
        findings = [
            f
            for f in findings
            if effective_safe_to_delete(f.confidence, f.file_path, f.safe_to_delete)
        ]
    return [DeadCodeFindingResponse.from_orm(f) for f in findings[:limit]]


@router.post("/api/repos/{repo_id}/dead-code/analyze", status_code=202)
async def analyze_dead_code(
    repo_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Trigger a fresh dead code analysis.

    Runs an index-only pipeline job (no LLM work) — dead-code detection is
    part of the analysis stage, so a re-index refreshes the findings. Returns
    202 with the job id; poll or stream it like any other job.
    """
    from sqlalchemy import select

    from repowise.core.persistence.models import GenerationJob
    from repowise.server.routers.repos import _launch_job_task

    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    active = await session.execute(
        select(GenerationJob.id)
        .where(GenerationJob.repository_id == repo_id)
        .where(GenerationJob.status.in_(["pending", "running"]))
        .limit(1)
    )
    if active.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409, detail="A job is already in progress for this repository"
        )

    job = await crud.upsert_generation_job(
        session,
        repository_id=repo_id,
        status="pending",
        config={"mode": "index_only", "source": "dead_code_analyze"},
    )
    # Commit so the background task's separate session can see the row.
    await session.commit()
    _launch_job_task(request, job.id, repo_id)
    return {"job_id": job.id, "status": "accepted", "repository_id": repo_id}


@router.get(
    "/api/repos/{repo_id}/dead-code/summary",
    response_model=DeadCodeSummaryResponse,
)
async def dead_code_summary(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> DeadCodeSummaryResponse:
    """Get aggregate dead code statistics for a repository."""
    summary = await crud.get_dead_code_summary(session, repo_id)
    return DeadCodeSummaryResponse(**summary)


@router.patch("/api/dead-code/{finding_id}", response_model=DeadCodeFindingResponse)
async def resolve_finding(
    finding_id: str,
    body: DeadCodePatchRequest,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> DeadCodeFindingResponse:
    """Update the status of a dead code finding."""
    valid_statuses = {"acknowledged", "resolved", "false_positive", "open"}
    if body.status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {sorted(valid_statuses)}",
        )

    finding = await crud.update_dead_code_status(session, finding_id, body.status, body.note)
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    return DeadCodeFindingResponse.from_orm(finding)
