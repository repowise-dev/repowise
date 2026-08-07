"""Overview + module-rollup routes."""

from __future__ import annotations

from datetime import datetime

from fastapi import Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.defect_accuracy import compute_defect_accuracy
from repowise.core.analysis.health.grading import band_for
from repowise.core.analysis.health.grading import distribution as health_distribution
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session
from repowise.server.mcp_server._meta import resolve_indexed_commit

from ._router import router
from .aggregation import _biomarker_breakdown, _module_rollups, _severity_breakdown
from .loaders import _attach_symbol_ids
from .serializers import _finding_to_dict, _leads_by_file, _metric_to_dict


def _resolve_last_indexed_at(
    snapshot_taken_at: datetime | None, repo_updated_at: datetime | None
) -> str | None:
    """Newest "index brought current" time as an ISO string, or ``None``.

    ``last_indexed_at`` should track the last time the index was synced to the
    checkout, not just the last health snapshot. A no-change ``repowise update``
    advances ``repositories.updated_at`` but takes no new snapshot, so a
    snapshot-only value would report the index as hours stale right after a
    refresh. Prefer whichever timestamp is newer (mirrors the overview router's
    sync fallback). Both inputs come from the same DB, so their tz-awareness
    matches and the comparison is safe.
    """
    newest = snapshot_taken_at
    if repo_updated_at is not None and (newest is None or repo_updated_at > newest):
        newest = repo_updated_at
    return newest.isoformat() if newest else None


@router.get("/api/repos/{repo_id}/health/overview")
async def health_overview(
    repo_id: str,
    limit: int = Query(20, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """KPIs + lowest-scoring files + per-module rollup + meta."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    # ``metrics=`` / ``findings=`` exist for exactly this caller (see the
    # parameter docstring): without them the summary pulls both whole tables a
    # second time. The metrics read also aggregates the deduction column for the
    # worst-first ranking, and the findings read is the most expensive of the
    # two — so the route was paying for each of them twice per request.
    metrics = await crud.get_health_metrics(session, repo_id)
    findings = await crud.get_health_findings(session, repo_id)
    summary = await crud.get_health_summary(
        session, repo_id, metrics=metrics, findings=findings
    )

    # Pull hotspot_health from the latest snapshot (KPIs aren't recomputed
    # on every overview hit — the snapshot is authoritative). Scalars only:
    # a full snapshot entity drags its per-file score map, and this route reads
    # none of it.
    snapshot = await crud.get_health_snapshot_headline(session, repo_id)
    hotspot_health = (
        round(snapshot.hotspot_health, 2) if snapshot.hotspot_health is not None else None
    )

    last_indexed_at = _resolve_last_indexed_at(snapshot.taken_at, repo.updated_at)

    leads = _leads_by_file(findings)
    metric_dicts = [_metric_to_dict(m, leads.get(m.file_path)) for m in metrics]

    # Repo-level band (from the NLOC-weighted average) + the per-band file
    # distribution. Both derive purely from the existing score — no new data.
    avg = summary.get("average_health")
    summary = {
        **summary,
        "hotspot_health": hotspot_health,
        "severity_breakdown": _severity_breakdown(findings),
        "band": band_for(float(avg)) if avg is not None else None,
    }
    distribution = health_distribution(metric_dicts)

    # "Does the score find the bugs?" self-validation, derived from the same
    # metrics + findings (prior_defect biomarker) already loaded above. ``None``
    # when the repo lacks enough files / defect history to be honest.
    #
    # Fed the rows it actually reads. It reads ``file_path`` / ``score`` off the
    # metrics — so ``metric_dicts`` above serves, no second conversion — and
    # only the ``prior_defect`` findings, so converting the other ~90% (which
    # means a ``json.loads`` of every ``details_json``) was pure waste.
    defect_accuracy = compute_defect_accuracy(
        metric_dicts,
        [_finding_to_dict(f) for f in findings if f.biomarker_type == "prior_defect"],
    )

    top_findings = await _attach_symbol_ids(
        session, repo_id, [_finding_to_dict(f) for f in findings[:limit]]
    )

    return {
        "summary": summary,
        "distribution": distribution,
        "defect_accuracy": defect_accuracy,
        "files": metric_dicts[:limit],
        "top_findings": top_findings,
        "modules": _module_rollups(metrics),
        "biomarkers": _biomarker_breakdown(findings),
        "meta": {
            "last_indexed_at": last_indexed_at,
            # Prefer state.json's last_sync_commit over a possibly-stale DB row
            # so the freshness signal self-heals on read (see the /api/repos
            # overlay). This is the extension's primary indexed-commit source.
            "head_commit": resolve_indexed_commit(repo.head_commit, repo.local_path),
            "snapshot_count": snapshot.snapshot_count,
        },
    }


@router.get("/api/repos/{repo_id}/health/modules")
async def health_modules(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """NLOC-weighted module rollups for the dashboard module section."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    metrics = await crud.get_health_metrics(session, repo_id)
    return {"modules": _module_rollups(metrics)}
