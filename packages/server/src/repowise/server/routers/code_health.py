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

from repowise.core.analysis.health.suggestions import suggestion_for as _suggestion_for
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


def _module_rollups(metrics: list[Any]) -> list[dict]:
    """NLOC-weighted module rollups derived from ``HealthFileMetric.module``."""
    buckets: dict[str, list[Any]] = {}
    for m in metrics:
        if m.module:
            buckets.setdefault(m.module, []).append(m)
    rows: list[dict] = []
    for name, group in buckets.items():
        total_nloc = sum(max(r.nloc, 1) for r in group)
        avg = sum(r.score * max(r.nloc, 1) for r in group) / total_nloc if total_nloc else 10.0
        worst = min(group, key=lambda r: r.score)
        rows.append(
            {
                "module": name,
                "file_count": len(group),
                "nloc": sum(r.nloc for r in group),
                "average_health": round(avg, 2),
                "worst_performer_path": worst.file_path,
                "worst_performer_score": round(worst.score, 2),
            }
        )
    rows.sort(key=lambda r: r["average_health"])
    return rows


@router.get("/api/repos/{repo_id}/health/overview")
async def health_overview(
    repo_id: str,
    limit: int = Query(20, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """KPIs + lowest-scoring files + per-module rollup."""
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
        "modules": _module_rollups(metrics),
    }


@router.get("/api/repos/{repo_id}/health/modules")
async def health_modules(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """NLOC-weighted module rollups for the dashboard module section."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    metrics = await crud.get_health_metrics(session, repo_id)
    return {"modules": _module_rollups(metrics)}


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
        # x score matrix without a second request.
        for f in files:
            m = metric_by_path.get(f["file_path"])
            if m is not None:
                f["health_score"] = round(m.score, 2)
                f["nloc"] = m.nloc

    # Aggregate by directory for module-level bars (cheap; one pass).
    modules: dict[str, dict[str, Any]] = {}
    for r in rows:
        mod = r.file_path.rsplit("/", 1)[0] if "/" in r.file_path else "(root)"
        bucket = modules.setdefault(mod, {"covered": 0, "total": 0, "files": 0})
        bucket["files"] += 1
        bucket["total"] += r.total_coverable_lines
        bucket["covered"] += round(r.line_coverage_pct / 100.0 * r.total_coverable_lines)
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


_EFFORT_BUCKETS: tuple[tuple[int, str], ...] = (
    (40, "S"),
    (150, "M"),
    (400, "L"),
)


def _effort_for_nloc(nloc: int) -> str:
    for ceiling, label in _EFFORT_BUCKETS:
        if nloc <= ceiling:
            return label
    return "XL"


@router.get("/api/repos/{repo_id}/health/refactoring-targets")
async def refactoring_targets(
    repo_id: str,
    limit: int = Query(50, ge=1, le=200),
    module: str | None = Query(None, description="Filter to files in this module path"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Refactoring candidates ranked by impact / effort.

    A *target* aggregates one file's findings: total health impact (the
    score deduction across all biomarkers) divided by an effort proxy
    (file NLOC bucket). The UI renders each target as a card with type,
    description, impact, and effort.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    metrics = await crud.get_health_metrics(session, repo_id)
    metric_by_path = {m.file_path: m for m in metrics}
    findings = await crud.get_health_findings(session, repo_id)

    by_file: dict[str, list[Any]] = {}
    for f in findings:
        by_file.setdefault(f.file_path, []).append(f)

    targets: list[dict] = []
    for file_path, fs in by_file.items():
        if module and not file_path.startswith(module):
            continue
        m = metric_by_path.get(file_path)
        nloc = m.nloc if m is not None else 0
        score = m.score if m is not None else 10.0
        # Largest hit per file becomes the headline biomarker; rest are
        # supporting evidence the UI can expand into.
        primary = max(fs, key=lambda x: x.health_impact)
        total_impact = round(sum(x.health_impact for x in fs), 3)
        effort_bucket = _effort_for_nloc(nloc)
        # Effort weight: S=1, M=2, L=3, XL=5 — keeps the ratio in a
        # human-readable shape on the card.
        weight = {"S": 1, "M": 2, "L": 3, "XL": 5}[effort_bucket]
        ratio = round(total_impact / weight, 3)
        targets.append(
            {
                "file_path": file_path,
                "score": round(score, 2),
                "nloc": nloc,
                "primary_biomarker": primary.biomarker_type,
                "primary_severity": primary.severity,
                "primary_reason": primary.reason,
                "primary_function": primary.function_name,
                "primary_line_start": primary.line_start,
                "primary_line_end": primary.line_end,
                "primary_suggestion": _suggestion_for(primary.biomarker_type),
                "total_impact": total_impact,
                "finding_count": len(fs),
                "biomarkers": sorted({x.biomarker_type for x in fs}),
                "effort_bucket": effort_bucket,
                "impact_per_effort": ratio,
            }
        )

    targets.sort(key=lambda t: (-t["impact_per_effort"], -t["total_impact"]))
    return {"targets": targets[:limit], "total": len(targets)}
