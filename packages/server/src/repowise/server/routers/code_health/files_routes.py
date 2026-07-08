"""Per-file health listing + single-file score breakdown routes."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.perf.coverage import supported_perf_languages
from repowise.core.analysis.health.suggestions import suggestion_for as _suggestion_for
from repowise.core.analysis.health.trends import file_trend
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session

from ._router import router
from .breakdown import _score_breakdown_from_findings
from .loaders import _attach_symbol_ids, _load_file_signals
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
    # Leads only for the page's files — enough to carry the top-reason chip and
    # the magnitude tiebreak without loading every finding for every row.
    page_paths = {m.file_path for m in page}
    findings = await crud.get_health_findings(session, repo_id)
    leads = _leads_by_file([f for f in findings if f.file_path in page_paths])
    # Per-file performance signal for the map's performance lens: open perf-
    # finding counts + whether a perf detector ran on the file's language. Colors
    # the lens by findings/coverage instead of the uniformly-green [9,10] score.
    perf_counts: dict[str, int] = {}
    for fnd in findings:
        if (getattr(fnd, "dimension", None) or "defect") == "performance":
            perf_counts[fnd.file_path] = perf_counts.get(fnd.file_path, 0) + 1
    lang_by_path = await crud.get_file_language_map(session, repo_id)
    perf_langs = supported_perf_languages()
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "files": [
            _metric_to_dict(
                m,
                leads.get(m.file_path),
                perf_findings=perf_counts.get(m.file_path, 0),
                perf_analyzed=lang_by_path.get(m.file_path) in perf_langs,
            )
            for m in page
        ],
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
    finding_dicts = await _attach_symbol_ids(
        session, repo_id, [_finding_to_dict(f) for f in findings]
    )
    snapshots = await crud.list_health_snapshots(session, repo_id)
    return {
        "file_path": file_path,
        "metric": _metric_to_dict(metric, _primary_and_magnitude(findings)) if metric else None,
        "breakdown": breakdown,
        "findings": finding_dicts,
        "suggestions": {b: _suggestion_for(b) for b in {f.biomarker_type for f in findings}},
        "trend": _file_trend_to_dict(file_trend(snapshots, file_path)),
        "signals": _file_signals_to_dict(await _load_file_signals(session, repo_id, file_path)),
    }
