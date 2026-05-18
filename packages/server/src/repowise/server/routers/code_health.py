"""/api/repos/{repo_id}/health/* — code-health endpoints.

Distinct from ``routers/health.py`` (liveness / Prometheus). All routes
here require API-key auth and operate on the ``health_findings`` /
``health_file_metrics`` tables.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.server.deps import get_db_session, verify_api_key

router = APIRouter(
    tags=["code-health"],
    dependencies=[Depends(verify_api_key)],
)


def _finding_to_dict(f: Any) -> dict:
    try:
        details = json.loads(f.details_json) if f.details_json else {}
    except Exception:
        details = {}
    return {
        "id": f.id,
        "file_path": f.file_path,
        "biomarker_type": f.biomarker_type,
        "severity": f.severity,
        "function_name": f.function_name,
        "line_start": f.line_start,
        "line_end": f.line_end,
        "health_impact": round(f.health_impact, 3),
        "reason": f.reason,
        "details": details,
        "status": f.status,
    }


def _metric_to_dict(m: Any) -> dict:
    return {
        "file_path": m.file_path,
        "score": round(m.score, 2),
        "max_ccn": m.max_ccn,
        "max_nesting": m.max_nesting,
        "nloc": m.nloc,
        "has_test_file": m.has_test_file,
        "line_coverage_pct": m.line_coverage_pct,
        "module": m.module,
    }


@router.get("/api/repos/{repo_id}/health/overview")
async def health_overview(
    repo_id: str,
    limit: int = Query(20, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """KPIs + lowest-scoring files."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    summary = await crud.get_health_summary(session, repo_id)
    metrics = await crud.get_health_metrics(session, repo_id)
    findings = await crud.get_health_findings(session, repo_id)
    return {
        "summary": summary,
        "files": [_metric_to_dict(m) for m in metrics[:limit]],
        "top_findings": [_finding_to_dict(f) for f in findings[:limit]],
    }


@router.get("/api/repos/{repo_id}/health/findings")
async def list_health_findings(
    repo_id: str,
    biomarker_type: str | None = Query(None),
    file_path: str | None = Query(None),
    min_severity: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[dict]:
    findings = await crud.get_health_findings(
        session,
        repo_id,
        biomarker_type=biomarker_type,
        file_path=file_path,
        min_severity=min_severity,
    )
    return [_finding_to_dict(f) for f in findings[:limit]]


@router.get("/api/repos/{repo_id}/health/files")
async def list_health_files(
    repo_id: str,
    limit: int = Query(200, ge=1, le=2000),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[dict]:
    metrics = await crud.get_health_metrics(session, repo_id)
    return [_metric_to_dict(m) for m in metrics[:limit]]
