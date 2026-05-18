"""/api/repos/{repo_id}/health/* — code-health endpoints.

Distinct from ``routers/health.py`` (liveness / Prometheus). All routes
here require API-key auth and operate on the ``health_findings`` /
``health_file_metrics`` tables.
"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.models import Severity
from repowise.core.analysis.health.scoring import (
    CATEGORY_CAPS,
    biomarker_category,
    severity_deduction,
)
from repowise.core.analysis.health.suggestions import suggestion_for as _suggestion_for
from repowise.core.analysis.health.trends import diff_snapshots, recent_kpis
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session, verify_api_key

router = APIRouter(
    tags=["code-health"],
    dependencies=[Depends(verify_api_key)],
)


_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


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
        "duplication_pct": getattr(m, "duplication_pct", None),
    }


# Strip the trailing " (N)" suffix that community detection appends to
# disambiguate same-named modules. The leak is harmless in the DB but
# noisy in the dashboard.
_MODULE_SUFFIX = re.compile(r"\s*\(\d+\)\s*$")


def _clean_module(name: str) -> str:
    return _MODULE_SUFFIX.sub("", name).strip()


def _module_rollups(metrics: list[Any]) -> list[dict]:
    """NLOC-weighted module rollups derived from ``HealthFileMetric.module``."""
    buckets: dict[str, list[Any]] = {}
    for m in metrics:
        if m.module:
            buckets.setdefault(_clean_module(m.module), []).append(m)
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


def _severity_breakdown(findings: list[Any]) -> dict[str, int]:
    out = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in findings:
        s = (f.severity or "").lower()
        if s in out:
            out[s] += 1
    return out


def _biomarker_breakdown(findings: list[Any]) -> list[dict]:
    """Per-biomarker counts split by severity, sorted by total."""
    by_type: dict[str, dict[str, int]] = {}
    for f in findings:
        b = f.biomarker_type
        sev = (f.severity or "").lower()
        bucket = by_type.setdefault(
            b, {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0}
        )
        if sev in bucket:
            bucket[sev] += 1
        bucket["total"] += 1
    rows = [{"biomarker_type": b, **counts} for b, counts in by_type.items()]
    rows.sort(key=lambda r: r["total"], reverse=True)
    return rows


@router.get("/api/repos/{repo_id}/health/overview")
async def health_overview(
    repo_id: str,
    limit: int = Query(20, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """KPIs + lowest-scoring files + per-module rollup + meta."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    summary = await crud.get_health_summary(session, repo_id)
    metrics = await crud.get_health_metrics(session, repo_id)
    findings = await crud.get_health_findings(session, repo_id)
    snapshots = await crud.list_health_snapshots(session, repo_id)

    # Pull hotspot_health from the latest snapshot (KPIs aren't recomputed
    # on every overview hit — the snapshot is authoritative).
    hotspot_health: float | None = None
    last_indexed_at: str | None = None
    if snapshots:
        latest = snapshots[-1]
        hotspot_health = round(float(latest.hotspot_health), 2)
        last_indexed_at = latest.taken_at.isoformat() if latest.taken_at else None

    summary = {
        **summary,
        "hotspot_health": hotspot_health,
        "severity_breakdown": _severity_breakdown(findings),
    }

    return {
        "summary": summary,
        "files": [_metric_to_dict(m) for m in metrics[:limit]],
        "top_findings": [_finding_to_dict(f) for f in findings[:limit]],
        "modules": _module_rollups(metrics),
        "biomarkers": _biomarker_breakdown(findings),
        "meta": {
            "last_indexed_at": last_indexed_at,
            "head_commit": repo.head_commit,
            "snapshot_count": len(snapshots),
        },
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


_SORT_FIELDS = {
    "score",
    "max_ccn",
    "max_nesting",
    "nloc",
    "duplication_pct",
    "line_coverage_pct",
    "file_path",
}


@router.get("/api/repos/{repo_id}/health/files")
async def list_health_files(
    repo_id: str,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    sort: str = Query("score", description="Sort field"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    search: str | None = Query(None, description="Substring filter on file_path"),
    module: str | None = Query(None, description="Filter to a module prefix"),
    only_hotspots: bool = Query(False),
    only_untested: bool = Query(False),
    only_failing: bool = Query(False, description="score < 7"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    if sort not in _SORT_FIELDS:
        sort = "score"
    metrics = await crud.get_health_metrics(session, repo_id)

    hotspot_paths: set[str] = set()
    if only_hotspots:
        git_meta = await crud.get_all_git_metadata(session, repo_id)
        hotspot_paths = {p for p, gm in git_meta.items() if getattr(gm, "is_hotspot", False)}

    def _keep(m: Any) -> bool:
        if search and search.lower() not in m.file_path.lower():
            return False
        if module and not m.file_path.startswith(module):
            return False
        if only_hotspots and m.file_path not in hotspot_paths:
            return False
        if only_untested and m.has_test_file:
            return False
        return not (only_failing and m.score >= 7)

    filtered = [m for m in metrics if _keep(m)]

    def _key(m: Any):
        v = getattr(m, sort, None)
        if v is None:
            return (1, 0) if order == "asc" else (0, 0)
        return (0, v) if order == "asc" else (0, -v if isinstance(v, (int, float)) else v)

    reverse = order == "desc" and sort == "file_path"
    if sort == "file_path":
        filtered.sort(key=lambda m: m.file_path, reverse=reverse)
    else:
        filtered.sort(key=_key, reverse=False)

    total = len(filtered)
    page = filtered[offset : offset + limit]
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "files": [_metric_to_dict(m) for m in page],
    }


def _score_breakdown_from_findings(findings: list[Any]) -> dict:
    """Recompute per-category deductions from open findings of one file.

    Mirrors ``scoring.score_file`` so the dashboard can show how a file's
    score was built up — even though the scoring math runs at index time,
    not at request time.
    """
    raw_per_cat: dict[str, list[tuple[Any, float]]] = {}
    for f in findings:
        sev = Severity(f.severity) if not isinstance(f.severity, Severity) else f.severity
        d = severity_deduction(sev)
        cat = biomarker_category(f.biomarker_type)
        raw_per_cat.setdefault(cat, []).append((f, d))

    categories: list[dict] = []
    total_deduction = 0.0
    for cat, cap in CATEGORY_CAPS.items():
        entries = raw_per_cat.get(cat, [])
        raw_sum = sum(d for _, d in entries)
        capped = min(raw_sum, cap)
        scale = (cap / raw_sum) if raw_sum > cap and raw_sum > 0 else 1.0
        categories.append(
            {
                "category": cat,
                "cap": round(cap, 2),
                "raw_deduction": round(raw_sum, 3),
                "applied_deduction": round(capped, 3),
                "capped": raw_sum > cap,
                "finding_count": len(entries),
                "findings": [
                    {
                        "id": f.id,
                        "biomarker_type": f.biomarker_type,
                        "severity": f.severity,
                        "raw_impact": round(d, 3),
                        "applied_impact": round(d * scale, 3),
                        "function_name": f.function_name,
                        "reason": f.reason,
                    }
                    for f, d in entries
                ],
            }
        )
        total_deduction += capped
    score = max(1.0, min(10.0, 10.0 - total_deduction))
    return {
        "score": round(score, 2),
        "total_deduction": round(total_deduction, 3),
        "categories": categories,
    }


@router.get("/api/repos/{repo_id}/health/files/breakdown")
async def file_score_breakdown(
    repo_id: str,
    file_path: str = Query(..., description="File path to break down"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    metrics = await crud.get_health_metrics(session, repo_id, file_paths=[file_path])
    metric = metrics[0] if metrics else None
    findings = await crud.get_health_findings(session, repo_id, file_path=file_path)
    breakdown = _score_breakdown_from_findings(findings)
    return {
        "file_path": file_path,
        "metric": _metric_to_dict(metric) if metric else None,
        "breakdown": breakdown,
        "findings": [_finding_to_dict(f) for f in findings],
        "suggestions": {
            b: _suggestion_for(b) for b in {f.biomarker_type for f in findings}
        },
    }


@router.get("/api/repos/{repo_id}/health/trend")
async def health_trend(
    repo_id: str,
    limit: int = Query(20, ge=1, le=50),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    snapshots = await crud.list_health_snapshots(session, repo_id)
    summary = diff_snapshots(snapshots)

    # Per-file delta from the last two snapshots.
    file_deltas: list[dict] = []
    if len(snapshots) >= 2:
        try:
            prev = json.loads(snapshots[-2].per_file_scores_json or "{}")
            cur = json.loads(snapshots[-1].per_file_scores_json or "{}")
        except Exception:
            prev, cur = {}, {}
        all_paths = set(prev) | set(cur)
        for p in all_paths:
            before = prev.get(p)
            after = cur.get(p)
            if before is None or after is None:
                continue
            d = round(float(after) - float(before), 2)
            if d == 0:
                continue
            file_deltas.append(
                {"file_path": p, "before": before, "after": after, "delta": d}
            )
        file_deltas.sort(key=lambda r: r["delta"])

    return {
        "history": recent_kpis(snapshots, limit=limit),
        "summary": {
            "current_hotspot_health": summary.current_hotspot_health,
            "current_average_health": summary.current_average_health,
            "previous_hotspot_health": summary.previous_hotspot_health,
            "previous_average_health": summary.previous_average_health,
            "hotspot_delta": summary.hotspot_delta,
            "average_delta": summary.average_delta,
        },
        "alerts": [
            {
                "kind": a.kind,
                "metric": a.metric,
                "current": a.current,
                "baseline": a.baseline,
                "delta": a.delta,
                "message": a.message,
            }
            for a in summary.alerts
        ],
        "file_deltas": file_deltas[:50],
        "snapshot_count": len(snapshots),
    }


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
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_ALLOWED_STATUSES)}")
    f = await crud.update_health_finding_status(session, finding_id, payload.status)
    if f is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    await session.commit()
    return _finding_to_dict(f)


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
    limit: int = Query(200, ge=1, le=500),
    module: str | None = Query(None, description="Filter to files in this module path"),
    biomarker: str | None = Query(None, description="Filter to one biomarker type"),
    min_severity: str | None = Query(None),
    max_effort: str | None = Query(None, description="S | M | L | XL"),
    sort: str = Query("impact_per_effort", pattern="^(impact_per_effort|total_impact|score|finding_count)$"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Refactoring candidates ranked by impact / effort."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    metrics = await crud.get_health_metrics(session, repo_id)
    metric_by_path = {m.file_path: m for m in metrics}
    findings = await crud.get_health_findings(session, repo_id)

    by_file: dict[str, list[Any]] = {}
    for f in findings:
        if biomarker and f.biomarker_type != biomarker:
            continue
        if min_severity:
            order = _SEVERITY_ORDER
            if order.get(f.severity, 0) < order.get(min_severity, 0):
                continue
        by_file.setdefault(f.file_path, []).append(f)

    effort_rank = {"S": 1, "M": 2, "L": 3, "XL": 5}
    max_effort_rank = effort_rank.get(max_effort or "", 99)

    targets: list[dict] = []
    for file_path, fs in by_file.items():
        if module and not file_path.startswith(module):
            continue
        m = metric_by_path.get(file_path)
        nloc = m.nloc if m is not None else 0
        score = m.score if m is not None else 10.0
        primary = max(fs, key=lambda x: x.health_impact)
        total_impact = round(sum(x.health_impact for x in fs), 3)
        effort_bucket = _effort_for_nloc(nloc)
        if effort_rank[effort_bucket] > max_effort_rank:
            continue
        weight = effort_rank[effort_bucket]
        ratio = round(total_impact / weight, 3)
        targets.append(
            {
                "file_path": file_path,
                "score": round(score, 2),
                "nloc": nloc,
                "module": _clean_module(m.module) if (m and m.module) else None,
                "primary_biomarker": primary.biomarker_type,
                "primary_severity": primary.severity,
                "primary_reason": primary.reason,
                "primary_function": primary.function_name,
                "primary_line_start": primary.line_start,
                "primary_line_end": primary.line_end,
                "primary_suggestion": _suggestion_for(primary.biomarker_type),
                "primary_finding_id": primary.id,
                "total_impact": total_impact,
                "finding_count": len(fs),
                "biomarkers": sorted({x.biomarker_type for x in fs}),
                "effort_bucket": effort_bucket,
                "impact_per_effort": ratio,
                "all_findings": [_finding_to_dict(f) for f in fs],
            }
        )

    sort_key_map = {
        "impact_per_effort": lambda t: (-t["impact_per_effort"], -t["total_impact"]),
        "total_impact": lambda t: -t["total_impact"],
        "score": lambda t: t["score"],
        "finding_count": lambda t: -t["finding_count"],
    }
    targets.sort(key=sort_key_map[sort])
    return {"targets": targets[:limit], "total": len(targets)}
