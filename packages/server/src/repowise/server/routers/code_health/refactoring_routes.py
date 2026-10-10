"""Health work/triage queue (impact / effort).

The legacy URL is retained for compatibility; these file-level findings are
not structured refactoring plans.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.effort import EFFORT_WEIGHT, effort_bucket
from repowise.core.analysis.health.impact_effort import build_impact_effort, fix_first_marks
from repowise.core.analysis.health.models import primary_finding, split_by_origin
from repowise.core.analysis.health.scope import parse_scope
from repowise.core.analysis.health.scoring import ZERO_IMPACT_DIMENSIONS
from repowise.core.analysis.health.suggestions import suggestion_for as _suggestion_for
from repowise.core.persistence import crud
from repowise.core.persistence.crud.analysis.fix_first import load_fix_first
from repowise.server.deps import get_db_session
from repowise.server.schemas import HealthWorkQueueResponse, ImpactEffortResponse

from ._router import router
from .counts import CountsQuery, project
from .file_filters import FAILING_DESCRIPTION, hotspot_paths, metric_filter
from .scope import ScopeQuery, narrow
from .statuses import STATUS_FILTER_DESCRIPTION, parse_status_filter

_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

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


def _finding_filter(q: QueueFilters) -> Callable[[Any], bool]:
    """One marker, then exact severities or else a severity floor."""
    # An all-empty list (","; " ") means the caller selected nothing, not that
    # nothing matches — falling through to ``min_severity`` keeps a stray
    # serialization from silently emptying the queue behind a 200.
    picked = {v.strip().lower() for v in (q.severity or "").split(",") if v.strip()}
    floor = _SEVERITY_ORDER.get(q.min_severity, 0) if q.min_severity else None

    def keep(f: Any) -> bool:
        if q.biomarker and f.biomarker_type != q.biomarker:
            return False
        if picked:
            return (f.severity or "").lower() in picked
        if floor is not None:
            return _SEVERITY_ORDER.get(f.severity, 0) >= floor
        return True

    return keep


def _group_by_file(findings: list[Any], keep: Callable[[Any], bool]) -> dict[str, list[Any]]:
    by_file: dict[str, list[Any]] = {}
    for f in findings:
        if keep(f):
            by_file.setdefault(f.file_path, []).append(f)
    return by_file


def _is_history_only(fs: list[Any], q: QueueFilters, history: str) -> bool:
    # Naming a marker reaches it whatever its origin, as with the zero-impact
    # dimensions in ``_load_queue_rows``.
    return history == "exclude" and q.biomarker is None and not split_by_origin(fs)[0]


def _lead_finding(fs: list[Any]) -> Any:
    primary = primary_finding(fs)
    if primary is not None:
        return primary
    # Every finding here is advisory, so no cause accuses this file and the
    # general queue never reaches this: advisory is excluded from it. A caller
    # who filtered to an advisory marker did reach it, and the marker they
    # asked for is the honest lead for the row.
    return max(fs, key=lambda x: (_SEVERITY_ORDER.get(x.severity, 0), -(x.line_start or 0)))


def _target_row(file_path: str, fs: list[Any], m: Any, bucket: str) -> dict:
    primary = _lead_finding(fs)
    # Impact, and therefore the ranking, counts only findings still open:
    # ``score`` on this row was computed from open findings, and a file
    # whose findings were all dismissed is not work to rank near the top.
    open_fs = [x for x in fs if (getattr(x, "status", None) or "open") == "open"]
    total_impact = round(sum(x.health_impact for x in open_fs), 3)
    return {
        "file_path": file_path,
        "score": round(m.score, 2),
        "nloc": m.nloc,
        "module": m.module or None,
        "is_test": bool(getattr(m, "is_test", False)),
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
        "effort_bucket": bucket,
        "impact_per_effort": round(total_impact / EFFORT_WEIGHT[bucket], 3),
    }


async def _queue_targets(
    session: AsyncSession, repo_id: str, q: QueueFilters, *, history: str
) -> tuple[list[dict], int]:
    """Every work item the filters keep, unsorted, and the history-only count."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    metric_by_path, findings = await _load_queue_rows(session, repo_id, q)
    by_file = _group_by_file(findings, _finding_filter(q))
    max_effort_rank = EFFORT_WEIGHT.get(q.max_effort or "", 99)

    targets: list[dict] = []
    history_only = 0
    for file_path, fs in by_file.items():
        m = metric_by_path.get(file_path)
        # Absent means either filtered out above, or a file this reading
        # cannot score: under ``code_shape``, a row with no recorded split.
        # Ranking that on a stand-in 10.0 would put an unmeasured file at the
        # top of a list ordered by how bad things are. The same holds for a
        # file in a language health has no dialect for, stored with no score.
        if m is None or m.score is None:
            continue
        if _is_history_only(fs, q, history):
            history_only += 1
            continue
        bucket = effort_bucket(m.nloc)
        if EFFORT_WEIGHT[bucket] > max_effort_rank:
            continue
        targets.append(_target_row(file_path, fs, m, bucket))
    return targets, history_only


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
    targets.sort(key=_SORT_KEYS[sort])
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
