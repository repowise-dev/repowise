"""Per-file health listing + single-file score breakdown routes."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.finding_registry import FILE_HOLDS, HELD_ORIGINS, split_gated
from repowise.core.analysis.health.perf.coverage import supported_perf_languages
from repowise.core.analysis.health.suggestions import suggestion_for as _suggestion_for
from repowise.core.analysis.health.trends import file_trend
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session

from .._unverified import UnverifiedQuery
from ._router import router
from .breakdown import _score_breakdown_from_findings
from .counts import CountsQuery, project
from .file_filters import FAILING_DESCRIPTION, hotspot_paths, metric_filter
from .loaders import _attach_symbol_ids, _load_file_signals
from .scope import ScopeQuery, narrow
from .serializers import (
    _file_signals_to_dict,
    _file_trend_to_dict,
    _finding_to_dict,
    _leads_by_file,
    _metric_to_dict,
    _primary_and_magnitude,
)

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
    only_failing: bool = Query(False, description=FAILING_DESCRIPTION),
    fields: str = Query(
        "full",
        pattern="^(full|summary)$",
        description=(
            "'summary' omits the optional per-row keys only the file table and "
            "drawer read (duplication_pct, defect_score, and the "
            "primary_biomarker / primary_reason / total_deduction lead), and "
            "narrows the finding read that produces them."
        ),
    ),
    scope: str = ScopeQuery,
    counts: str = CountsQuery,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    if sort not in _SORT_FIELDS:
        sort = "score"
    metrics = await crud.get_health_metrics(session, repo_id)
    (metrics,) = narrow(scope, metrics)
    # Projected before every filter and the sort, so `total` and the ranking
    # describe the same population the score does.
    metrics, _unscored = project(counts, metrics)

    keep = metric_filter(
        search=search,
        module=module,
        only_hotspots=only_hotspots,
        only_untested=only_untested,
        only_failing=only_failing,
        hotspots=await hotspot_paths(session, repo_id) if only_hotspots else None,
    )
    filtered = [m for m in metrics if keep(m)]

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
    summary = fields == "summary"
    # The full row's dominant-cause lead is reduced from *every* open finding
    # (this repo: 10,740 rows, 1.66 MB of ``details_json`` hydrated to reach four
    # attributes). A caller that does not print the lead should not pay for the
    # wide read to compute it — the map is exactly that caller, and it is the
    # biggest request the page makes.
    leads: dict[str, dict] = {}
    if not summary:
        # History markers kept: a file's lead names what its score counted, so a
        # history-only file scoring below 10 still has one.
        findings = await crud.get_health_findings(session, repo_id, hold_history=False)
        # Leads only for the page's files — enough to carry the top-reason chip
        # and the magnitude tiebreak without loading every finding for every row.
        page_paths = {m.file_path for m in page}
        leads = _leads_by_file([f for f in findings if f.file_path in page_paths])
    # Per-file performance signal for the map's performance lens: causes in the
    # default queue (what the performance headline leads with) + whether a perf
    # detector ran on the file's language, instead of the narrow-band score.
    queued = {r.file_path: r.queued for r in await crud.performance_file_rollups(session, repo_id)}
    lang_by_path = await crud.get_file_language_map(session, repo_id)
    perf_langs = supported_perf_languages()
    # Read once per request, like the MCP rows; only the full row carries it.
    short_history = set() if summary else await crud.get_short_history_paths(session, repo_id)
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "files": [
            _held_row(
                _metric_to_dict(
                    m,
                    leads.get(m.file_path),
                    perf_opportunities=queued.get(m.file_path, 0),
                    perf_analyzed=lang_by_path.get(m.file_path) in perf_langs,
                    summary=summary,
                    history_basis="short" if m.file_path in short_history else None,
                ),
                m,
            )
            for m in page
        ],
        # What the finding reads above left out, by language or file hold.
        "gated": await crud.gated_health_counts(session, repo_id),
    }


def _held_row(row: dict, metric: Any) -> dict:
    """*row*, saying why its findings are not listed when its file's origin
    holds every one of them back (its score still counts them)."""
    origin = getattr(metric, "code_origin", None)
    if origin in HELD_ORIGINS:
        row["held_reason"] = FILE_HOLDS[origin]
    return row


@router.get("/api/repos/{repo_id}/health/files/breakdown")
async def file_score_breakdown(
    repo_id: str,
    file_path: str = Query(..., description="File path to break down"),
    counts: str = CountsQuery,
    include_unverified: bool = UnverifiedQuery,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    metrics = await crud.get_health_metrics(session, repo_id, file_paths=[file_path])
    findings = await crud.get_health_findings(
        session, repo_id, file_path=file_path, include_unverified=True
    )
    files = {m.file_path: m for m in metrics}
    # The score counts every finding; a gated one is held back from the list
    # and the breakdown, and ``gated`` says how many. History markers are held
    # from the list only: the breakdown and the lead still sum what the score
    # counted.
    scored, _ = split_gated(
        "health",
        findings,
        kind="biomarker_type",
        include_unverified=include_unverified,
        files=files,
        hold_history=False,
    )
    findings, gated = split_gated(
        "health",
        findings,
        kind="biomarker_type",
        include_unverified=include_unverified,
        files=files,
    )
    # This drawer opens from a row the reader just saw a score on. Reading it
    # under the other counts would answer a click on 8.5 with a 2.0 and list
    # the findings the page had just said were excluded.
    metrics, scored, findings, _unscored = project(counts, metrics, scored, findings)
    metric = metrics[0] if metrics else None
    # No breakdown of a score the file does not have (no health dialect for
    # its language): one rebuilt from its findings would read 10.0.
    breakdown = (
        None
        if metric is not None and metric.score is None
        else _score_breakdown_from_findings(scored)
    )
    finding_dicts = await _attach_symbol_ids(
        session, repo_id, [_finding_to_dict(f) for f in findings]
    )
    snapshots = await crud.list_health_snapshots(
        session, repo_id, limit=crud.FILE_TREND_SNAPSHOT_WINDOW
    )
    return {
        "file_path": file_path,
        "metric": _metric_to_dict(metric, _primary_and_magnitude(scored)) if metric else None,
        "breakdown": breakdown,
        "findings": finding_dicts,
        "gated": gated,
        "suggestions": {b: _suggestion_for(b) for b in {f.biomarker_type for f in findings}},
        "trend": _file_trend_to_dict(file_trend(snapshots, file_path)),
        "signals": _file_signals_to_dict(await _load_file_signals(session, repo_id, file_path)),
    }
