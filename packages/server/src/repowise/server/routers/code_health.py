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


def _coverage_row_to_dict(row: Any, *, include_covered_lines: bool = False) -> dict:
    out: dict[str, Any] = {
        "file_path": row.file_path,
        "source_format": row.source_format,
        "line_coverage_pct": row.line_coverage_pct,
        "branch_coverage_pct": row.branch_coverage_pct,
        "total_coverable_lines": row.total_coverable_lines,
        "ingested_at": row.ingested_at.isoformat() if row.ingested_at else None,
        "ingested_commit_sha": row.ingested_commit_sha,
    }
    if include_covered_lines:
        try:
            out["covered_lines"] = json.loads(row.covered_lines_json or "[]")
        except Exception:
            out["covered_lines"] = []
    return out


@router.get("/api/repos/{repo_id}/health/coverage")
async def health_coverage(
    repo_id: str,
    file_path: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Coverage summary + per-file rows.

    Pass ``file_path`` to fetch a single file's full covered-line set.
    Without ``file_path`` we return the summary + a list of per-file
    rows trimmed by ``limit`` (covered_lines arrays stripped).
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    summary = await crud.get_coverage_summary(session, repo_id)
    if summary.get("ingested_at") is not None:
        summary = {**summary, "ingested_at": summary["ingested_at"].isoformat()}
    rows = await crud.load_coverage_for_repo(
        session, repo_id, file_paths=[file_path] if file_path else None
    )
    metrics = await crud.get_health_metrics(session, repo_id)
    metric_by_path = {m.file_path: m for m in metrics}

    if file_path:
        files = [_coverage_row_to_dict(r, include_covered_lines=True) for r in rows]
    else:
        rows_sorted = sorted(rows, key=lambda r: r.line_coverage_pct)
        files = [_coverage_row_to_dict(r) for r in rows_sorted[:limit]]
        # Attach per-file health score so the UI can render a coverage
        # × score matrix without a second request.
        for f in files:
            m = metric_by_path.get(f["file_path"])
            if m is not None:
                f["health_score"] = round(m.score, 2)
                f["nloc"] = m.nloc

    # Aggregate by directory for module-level bars (cheap; one pass).
    modules: dict[str, dict[str, Any]] = {}
    for r in rows:
        mod = r.file_path.rsplit("/", 1)[0] if "/" in r.file_path else "(root)"
        bucket = modules.setdefault(
            mod, {"covered": 0, "total": 0, "files": 0}
        )
        bucket["files"] += 1
        bucket["total"] += r.total_coverable_lines
        bucket["covered"] += int(
            round(r.line_coverage_pct / 100.0 * r.total_coverable_lines)
        )
    module_rows = [
        {
            "module": name,
            "files": v["files"],
            "covered_lines": v["covered"],
            "total_lines": v["total"],
            "line_coverage_pct": (
                round(v["covered"] / v["total"] * 100.0, 2) if v["total"] else 0.0
            ),
        }
        for name, v in modules.items()
    ]
    module_rows.sort(key=lambda x: x["line_coverage_pct"])

    return {
        "summary": summary,
        "files": files,
        "modules": module_rows[:limit],
    }
