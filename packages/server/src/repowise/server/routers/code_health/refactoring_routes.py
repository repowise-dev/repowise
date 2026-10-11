"""Health work/triage queue (impact / effort).

The legacy URL is retained for compatibility; these file-level findings are
not structured refactoring plans.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.impact_effort import (
    WORK_QUEUE_SORTS,
    build_impact_effort,
    fix_first_marks,
    work_queue_targets,
)
from repowise.core.analysis.health.scope import parse_scope
from repowise.core.analysis.health.scoring import ZERO_IMPACT_DIMENSIONS
from repowise.core.persistence import crud
from repowise.core.persistence.crud.analysis.fix_first import load_fix_first
from repowise.server.deps import get_db_session
from repowise.server.schemas import HealthWorkQueueResponse, ImpactEffortResponse

from ._router import router
from .counts import CountsQuery, project
from .file_filters import FAILING_DESCRIPTION, hotspot_paths, metric_filter
from .scope import ScopeQuery, narrow
from .statuses import STATUS_FILTER_DESCRIPTION, parse_status_filter


@dataclass(frozen=True, slots=True)
class QueueFilters:
    """The narrowing the work queue and its impact / effort plane share."""

    module: str | None
    biomarker: str | None
    min_severity: str | None
    severity: str | None
    dimension: str | None
    status: str
    search: str | None
    only_hotspots: bool
    only_untested: bool
    only_failing: bool
    max_effort: str | None
    scope: str
    counts: str


def queue_filters(
    module: str | None = Query(None, description="Filter to files in this module path"),
    biomarker: str | None = Query(None, description="Filter to one biomarker type"),
    min_severity: str | None = Query(None, description="Severity floor"),
    severity: str | None = Query(
        None, description="Exact severities, comma-separated. Overrides min_severity."
    ),
    dimension: str | None = Query(
        None, description="defect | maintainability | performance | advisory"
    ),
    status: str = Query("open", description=STATUS_FILTER_DESCRIPTION),
    search: str | None = Query(None, description="Substring filter on file_path"),
    only_hotspots: bool = Query(False),
    only_untested: bool = Query(False),
    only_failing: bool = Query(False, description=FAILING_DESCRIPTION),
    max_effort: str | None = Query(None, description="S | M | L | XL"),
    scope: str = ScopeQuery,
    counts: str = CountsQuery,
) -> QueueFilters:
    return QueueFilters(
        module=module,
        biomarker=biomarker,
        min_severity=min_severity,
        severity=severity,
        dimension=dimension,
        status=status,
        search=search,
        only_hotspots=only_hotspots,
        only_untested=only_untested,
        only_failing=only_failing,
        max_effort=max_effort,
        scope=scope,
        counts=counts,
    )


async def _load_queue_rows(
    session: AsyncSession, repo_id: str, q: QueueFilters
) -> tuple[dict[str, Any], list[Any]]:
    """The metrics the file-level filters keep, by path, and the findings read."""
    metrics = await crud.get_health_metrics(session, repo_id)
    # As the findings list does, so a row's count and the list behind it agree:
    # the zero-impact dimensions stay out of the ranking, and naming one thing
    # -- here a marker -- takes the caller out of that ranking and returns it.
    # The marker options come from the unfiltered breakdown, so without this
    # every advisory and performance marker in that menu matches nothing.
    findings = await crud.get_health_findings(
        session,
        repo_id,
        dimension=q.dimension,
        status=parse_status_filter(q.status),
        exclude_dimensions=(
            tuple(sorted(ZERO_IMPACT_DIMENSIONS)) if q.biomarker is None else None
        ),
    )
    metrics, findings = narrow(q.scope, metrics, findings)
    metrics, findings, _unscored = project(q.counts, metrics, findings)

    keep_metric = metric_filter(
        search=q.search,
        module=q.module,
        only_hotspots=q.only_hotspots,
        only_untested=q.only_untested,
        only_failing=q.only_failing,
        hotspots=await hotspot_paths(session, repo_id) if q.only_hotspots else None,
    )
    return {m.file_path: m for m in metrics if keep_metric(m)}, findings


async def _queue_targets(
    session: AsyncSession, repo_id: str, q: QueueFilters, *, history: str
) -> tuple[list[dict], int]:
    """Every work item the filters keep, unsorted, and the history-only count."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    metric_by_path, findings = await _load_queue_rows(session, repo_id, q)
    return work_queue_targets(
        metric_by_path,
        findings,
        biomarker=q.biomarker,
        severity=q.severity,
        min_severity=q.min_severity,
        max_effort=q.max_effort,
        history=history,
    )


_HISTORY_QUERY = Query(
    "exclude",
    pattern="^(exclude|include)$",
    description=(
        "exclude (default) leaves out files whose only findings are history "
        "markers: context, not work an edit can do. include keeps them."
    ),
)


@router.get(
    "/api/repos/{repo_id}/health/refactoring-targets",
    response_model=HealthWorkQueueResponse,
)
async def health_work_queue(
    repo_id: str,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: str = Query(
        "impact_per_effort", pattern="^(impact_per_effort|total_impact|score|finding_count)$"
    ),
    history: str = _HISTORY_QUERY,
    filters: QueueFilters = Depends(queue_filters),
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
    targets, history_only = await _queue_targets(session, repo_id, filters, history=history)
    targets.sort(key=WORK_QUEUE_SORTS[sort])
    # Both counts, because the view lists files but triages findings: "50 of
    # 812 files" alone leaves the size of the work unsaid.
    return {
        "targets": targets[offset : offset + limit],
        "total": len(targets),
        "finding_total": sum(t["finding_count"] for t in targets),
        "history_only_excluded": history_only,
        "offset": offset,
        "limit": limit,
    }


@router.get(
    "/api/repos/{repo_id}/health/impact-effort",
    response_model=ImpactEffortResponse,
)
async def health_impact_effort(
    repo_id: str,
    filters: QueueFilters = Depends(queue_filters),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Every file the work queue's filters keep, placed by effort and gain.

    The whole filtered set, not a page, so the plane and the queue's header
    count describe the same files. History-only files are always left out:
    nothing in their code can be changed to recover the health they cost.
    """
    targets, history_only = await _queue_targets(session, repo_id, filters, history="exclude")
    paths = [t["file_path"] for t in targets]
    opportunities: list[Any] = []
    if paths:
        opportunities, _n = await crud.list_refactoring_opportunities(
            session, repo_id, file_paths=paths, limit=len(paths)
        )
    # The Fix-first card on the same page builds this queue too, so this is
    # normally a cache hit. Only the items it shows are marked on the plane.
    queue = await load_fix_first(
        session,
        repo_id,
        scope="production" if parse_scope(filters.scope) == "production" else "all",
    )
    plane = build_impact_effort(targets, opportunities, fix_first_marks(queue.items))
    return {**plane.as_dict(), "history_only_excluded": history_only}
