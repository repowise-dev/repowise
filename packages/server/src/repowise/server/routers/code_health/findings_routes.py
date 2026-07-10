"""Findings list + status-update routes."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.server.deps import get_db_session

from ._router import router
from .loaders import _attach_symbol_ids
from .serializers import _finding_to_dict


@router.get("/api/repos/{repo_id}/health/findings")
async def list_health_findings(
    repo_id: str,
    biomarker_type: str | None = Query(None),
    file_path: str | None = Query(None),
    min_severity: str | None = Query(None),
    dimension: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[dict]:
    findings = await crud.get_health_findings(
        session,
        repo_id,
        biomarker_type=biomarker_type,
        file_path=file_path,
        min_severity=min_severity,
        dimension=dimension,
    )
    return await _attach_symbol_ids(
        session, repo_id, [_finding_to_dict(f) for f in findings[:limit]]
    )


class FindingStatusUpdate(BaseModel):
    status: str = Field(..., description="open | acknowledged | resolved | false_positive")


_ALLOWED_STATUSES = {"open", "acknowledged", "resolved", "false_positive"}


@router.patch("/api/repos/{repo_id}/health/findings/{finding_id}")
async def update_finding_status(
    repo_id: str,
    finding_id: str,
    payload: FindingStatusUpdate,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    if payload.status not in _ALLOWED_STATUSES:
        raise HTTPException(
            status_code=400, detail=f"status must be one of {sorted(_ALLOWED_STATUSES)}"
        )
    f = await crud.update_health_finding_status(session, finding_id, payload.status)
    if f is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    await session.commit()
    return _finding_to_dict(f)
