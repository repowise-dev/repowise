"""Findings list + status-update routes."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.counts import parse_counts
from repowise.core.analysis.health.models import split_by_origin
from repowise.core.analysis.health.scope import parse_scope
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session
from repowise.server.schemas import (
    HealthFindingResponse,
    HealthFindingWithSymbolResponse,
)

from ._router import router
from .counts import CountsQuery
from .loaders import _attach_symbol_ids
from .scope import ScopeQuery, narrow
from .serializers import _finding_to_dict
from .statuses import (
    ALLOWED_STATUSES,
    STATUS_FILTER_DESCRIPTION,
    parse_status_filter,
)


@router.get(
    "/api/repos/{repo_id}/health/findings",
    response_model=list[HealthFindingWithSymbolResponse],
)
async def list_health_findings(
    repo_id: str,
    biomarker_type: str | None = Query(None),
    file_path: str | None = Query(None),
    min_severity: str | None = Query(None, description="Severity floor"),
    severity: str | None = Query(
        None, description="Exact severities, comma-separated. Overrides min_severity."
    ),
    dimension: str | None = Query(None),
    status: str = Query("open", description=STATUS_FILTER_DESCRIPTION),
    limit: int = Query(100, ge=1, le=1000),
    scope: str = ScopeQuery,
    counts: str = CountsQuery,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """Findings, ranked by health impact. Open work unless ``status`` says otherwise.

    Performance is out of the unfiltered list by default. Its findings carry a
    health impact of zero by construction, so ranking them here sorts them
    below every defect row and reads as "nothing here" rather than as a
    different unit; the performance surfaces rank the same evidence by cause.
    Asking for ``dimension=performance`` still returns it.
    """
    findings = await crud.get_health_findings(
        session,
        repo_id,
        biomarker_type=biomarker_type,
        file_path=file_path,
        min_severity=min_severity,
        severity=severity,
        dimension=dimension,
        status=parse_status_filter(status),
        exclude_dimensions=("performance",),
    )
    # A finding carries a path, not ``is_test``, so narrowing it needs the
    # metric rows that do. Read them only when the answer depends on them:
    # the default scope returns the same list either way.
    if parse_scope(scope) == "production":
        metrics = await crud.get_health_metrics(session, repo_id)
        _, findings = narrow(scope, metrics, findings)
    # A history finding contributes nothing to a code-shape score, so listing
    # it under one would show work that sums past the figure above it.
    if parse_counts(counts) == "code_shape":
        findings = split_by_origin(findings)[0]
    return await _attach_symbol_ids(
        session, repo_id, [_finding_to_dict(f) for f in findings[:limit]]
    )


class FindingStatusUpdate(BaseModel):
    status: str = Field(..., description="open | acknowledged | resolved | false_positive")


@router.patch(
    "/api/repos/{repo_id}/health/findings/{finding_id}",
    response_model=HealthFindingResponse,
)
async def update_finding_status(
    repo_id: str,
    finding_id: str,
    payload: FindingStatusUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    if payload.status not in ALLOWED_STATUSES:
        raise HTTPException(
            status_code=400, detail=f"status must be one of {sorted(ALLOWED_STATUSES)}"
        )
    f = await crud.update_health_finding_status(session, finding_id, payload.status)
    if f is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    await session.commit()
    return _finding_to_dict(f)
