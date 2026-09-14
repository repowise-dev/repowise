"""Health work/triage queue (impact / effort).

The legacy URL is retained for compatibility; these file-level findings are
not structured refactoring plans.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.models import primary_finding
from repowise.core.analysis.health.suggestions import suggestion_for as _suggestion_for
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session
from repowise.server.schemas import HealthWorkQueueResponse

from ._router import router
from .counts import CountsQuery, project
from .file_filters import FAILING_DESCRIPTION, hotspot_paths, metric_filter
from .scope import ScopeQuery, narrow
from .statuses import STATUS_FILTER_DESCRIPTION, parse_status_filter

_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

_EFFORT_BUCKETS: tuple[tuple[int, str], ...] = (
    (40, "S"),
    (150, "M"),
    (400, "L"),
)


_SORT_KEYS = {
    "impact_per_effort": lambda t: (-t["impact_per_effort"], -t["total_impact"]),
    "total_impact": lambda t: -t["total_impact"],
    # ``score`` clamps at 1.0, so on a real repo dozens of targets tie at the
    # top and sorting on it alone returns them in dict-insertion order.
    # ``total_impact`` is the same pre-clamp deduction magnitude the crud layer
    # ranks metrics by — but summed over the findings that survived this
    # request's finding-level filters (biomarker, severity, min_severity,
    # dimension, status), so under a filter this ranks by the filtered depth
    # rather than the file's full depth. That is what a filtered queue should
    # do; it just means the order is not expected to match /health/files once
    # a filter is on.
    "score": lambda t: (t["score"], -t["total_impact"], t["file_path"]),
    "finding_count": lambda t: -t["finding_count"],
}


def _effort_for_nloc(nloc: int) -> str:
    for ceiling, label in _EFFORT_BUCKETS:
        if nloc <= ceiling:
            return label
    return "XL"


@router.get(
    "/api/repos/{repo_id}/health/refactoring-targets",
    response_model=HealthWorkQueueResponse,
)
async def health_work_queue(
    repo_id: str,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    module: str | None = Query(None, description="Filter to files in this module path"),
    biomarker: str | None = Query(None, description="Filter to one biomarker type"),
    min_severity: str | None = Query(None, description="Severity floor"),
    severity: str | None = Query(
        None, description="Exact severities, comma-separated. Overrides min_severity."
    ),
    dimension: str | None = Query(None, description="defect | maintainability | performance"),
    status: str = Query("open", description=STATUS_FILTER_DESCRIPTION),
    search: str | None = Query(None, description="Substring filter on file_path"),
    only_hotspots: bool = Query(False),
    only_untested: bool = Query(False),
    only_failing: bool = Query(False, description=FAILING_DESCRIPTION),
    max_effort: str | None = Query(None, description="S | M | L | XL"),
    sort: str = Query(
        "impact_per_effort", pattern="^(impact_per_effort|total_impact|score|finding_count)$"
    ),
    scope: str = ScopeQuery,
    counts: str = CountsQuery,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Health work items ranked by impact / effort.

    A target carries its *primary* finding plus ``finding_count``, not the
    findings themselves. Serializing every file's full finding list here cost
    1.8 MB at the default ``limit=200`` (2.9 MB at 500) to render a list that
    shows none of it — the work was done for all ~2,400 files with findings,
    before the ``[:limit]`` slice, and ~90% was then discarded. The two
    consumers both sit behind a click and fetch what they need from
    ``GET /health/findings?file_path=``.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    metrics = await crud.get_health_metrics(session, repo_id)
    findings = await crud.get_health_findings(
        session,
        repo_id,
        dimension=dimension,
        status=parse_status_filter(status),
        # As the findings list does, so a row's count and the list behind it
        # agree. Performance carries zero health impact and ranks by cause on
        # its own surface; ``dimension=performance`` still returns it.
        exclude_dimensions=("performance",),
    )
    metrics, findings = narrow(scope, metrics, findings)
    metrics, findings, _unscored = project(counts, metrics, findings)

    keep_metric = metric_filter(
        search=search,
        module=module,
        only_hotspots=only_hotspots,
        only_untested=only_untested,
        only_failing=only_failing,
        hotspots=await hotspot_paths(session, repo_id) if only_hotspots else None,
    )
    metric_by_path = {m.file_path: m for m in metrics if keep_metric(m)}

    # An all-empty list (","; " ") means the caller selected nothing, not that
    # nothing matches — falling through to ``min_severity`` keeps a stray
    # serialization from silently emptying the queue behind a 200.
    picked = {v.strip().lower() for v in (severity or "").split(",") if v.strip()}
    exact_severities = picked or None

    by_file: dict[str, list[Any]] = {}
    for f in findings:
        if biomarker and f.biomarker_type != biomarker:
            continue
        if exact_severities is not None:
            if (f.severity or "").lower() not in exact_severities:
                continue
        elif min_severity:
            order = _SEVERITY_ORDER
            if order.get(f.severity, 0) < order.get(min_severity, 0):
                continue
        by_file.setdefault(f.file_path, []).append(f)

    effort_rank = {"S": 1, "M": 2, "L": 3, "XL": 5}
    max_effort_rank = effort_rank.get(max_effort or "", 99)

    targets: list[dict] = []
    for file_path, fs in by_file.items():
        m = metric_by_path.get(file_path)
        # Absent means either filtered out above, or a file this reading
        # cannot score: under ``code_shape``, a row with no recorded split.
        # Ranking that on a stand-in 10.0 would put an unmeasured file at the
        # top of a list ordered by how bad things are.
        if m is None:
            continue
        nloc = m.nloc
        score = m.score
        primary = primary_finding(fs)
        # Impact, and therefore the ranking, counts only findings still open:
        # ``score`` on this row was computed from open findings, and a file
        # whose findings were all dismissed is not work to rank near the top.
        open_fs = [x for x in fs if (getattr(x, "status", None) or "open") == "open"]
        total_impact = round(sum(x.health_impact for x in open_fs), 3)
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
                "module": m.module if (m and m.module) else None,
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
                "open_finding_count": len(open_fs),
                "biomarkers": sorted({x.biomarker_type for x in fs}),
                "effort_bucket": effort_bucket,
                "impact_per_effort": ratio,
            }
        )

    targets.sort(key=_SORT_KEYS[sort])
    # Both counts, because the view lists files but triages findings: "50 of
    # 812 files" alone leaves the size of the work unsaid.
    return {
        "targets": targets[offset : offset + limit],
        "total": len(targets),
        "finding_total": sum(t["finding_count"] for t in targets),
        "offset": offset,
        "limit": limit,
    }
