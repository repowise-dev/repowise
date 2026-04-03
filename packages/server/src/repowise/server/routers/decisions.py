"""/api/repos/{repo_id}/decisions — Architectural decision record endpoints."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, Query
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import (
    DecisionCreate,
    DecisionRecordResponse,
    DecisionStatusUpdate,
)

router = APIRouter(
    tags=["decisions"],
    dependencies=[Depends(verify_api_key)],
)


@router.get(
    "/api/repos/{repo_id}/decisions",
    response_model=list[DecisionRecordResponse],
)
async def list_decisions(
    repo_id: str,
    status: str | None = Query(None, description="Filter by status"),
    source: str | None = Query(None, description="Filter by source"),
    tag: str | None = Query(None, description="Filter by tag"),
    module: str | None = Query(None, description="Filter by module path"),
    include_proposed: bool = Query(True),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[DecisionRecordResponse]:
    """List architectural decision records for a repository."""
    decisions = await crud.list_decisions(
        session,
        repo_id,
        status=status,
        source=source,
        tag=tag,
        module=module,
        include_proposed=include_proposed,
        limit=limit,
        offset=offset,
    )
    return [DecisionRecordResponse.from_orm(d) for d in decisions]


@router.get(
    "/api/repos/{repo_id}/decisions/health",
)
async def decision_health(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Get decision health summary: stale, proposed, ungoverned hotspots."""
    summary = await crud.get_decision_health_summary(session, repo_id)
    return {
        "summary": summary["summary"],
        "stale_decisions": [DecisionRecordResponse.from_orm(d) for d in summary["stale_decisions"]],
        "proposed_awaiting_review": [
            DecisionRecordResponse.from_orm(d) for d in summary["proposed_awaiting_review"]
        ],
        "ungoverned_hotspots": summary["ungoverned_hotspots"],
    }


@router.get(
    "/api/repos/{repo_id}/decisions/{decision_id}",
    response_model=DecisionRecordResponse,
)
async def get_decision(
    repo_id: str,
    decision_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> DecisionRecordResponse:
    """Get a single decision record by ID."""
    rec = await crud.get_decision(session, decision_id)
    if rec is None or rec.repository_id != repo_id:
        raise HTTPException(status_code=404, detail="Decision not found")
    return DecisionRecordResponse.from_orm(rec)


@router.post(
    "/api/repos/{repo_id}/decisions",
    response_model=DecisionRecordResponse,
    status_code=201,
)
async def create_decision(
    repo_id: str,
    body: DecisionCreate,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> DecisionRecordResponse:
    """Create a new decision record (e.g. from CLI capture via API)."""
    rec = await crud.upsert_decision(
        session,
        repository_id=repo_id,
        title=body.title,
        status="active",
        context=body.context,
        decision=body.decision,
        rationale=body.rationale,
        alternatives=body.alternatives,
        consequences=body.consequences,
        affected_files=body.affected_files,
        affected_modules=body.affected_modules,
        tags=body.tags,
        source="cli",
        confidence=1.0,
    )
    return DecisionRecordResponse.from_orm(rec)


@router.patch(
    "/api/repos/{repo_id}/decisions/{decision_id}",
    response_model=DecisionRecordResponse,
)
async def patch_decision(
    repo_id: str,
    decision_id: str,
    body: DecisionStatusUpdate,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> DecisionRecordResponse:
    """Update the status of a decision record (confirm, deprecate, supersede)."""
    try:
        rec = await crud.update_decision_status(
            session,
            decision_id,
            body.status,
            superseded_by=body.superseded_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if rec is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    if rec.repository_id != repo_id:
        raise HTTPException(status_code=404, detail="Decision not found")
    return DecisionRecordResponse.from_orm(rec)
