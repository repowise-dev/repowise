"""Refactoring-target ranking route (impact / effort)."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.suggestions import suggestion_for as _suggestion_for
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session

from ._router import router
from .aggregation import _clean_module
from .serializers import _finding_to_dict

_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

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
    sort: str = Query(
        "impact_per_effort", pattern="^(impact_per_effort|total_impact|score|finding_count)$"
    ),
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
