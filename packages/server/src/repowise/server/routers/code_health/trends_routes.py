"""File-level + repo-level health trend routes."""

from __future__ import annotations

import json

from fastapi import Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.trends import diff_snapshots, file_trend, recent_kpis
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session
from repowise.server.schemas import FileHealthTrendResponse, HealthTrendResponse

from ._router import router
from .serializers import _file_trend_to_dict

# How many per-file movements the trend response carries. Paired with
# ``file_deltas_total`` so a caller can always tell a full list from a slice.
FILE_DELTA_LIMIT: int = 50


@router.get(
    "/api/repos/{repo_id}/health/files/trend",
    response_model=FileHealthTrendResponse,
)
async def file_health_trend(
    repo_id: str,
    file_path: str = Query(..., description="File path to chart over time"),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """A single file's score-over-time series from the snapshot history.

    Silent (empty ``points``) when fewer than two snapshots carry the file.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    snapshots = await crud.list_health_snapshots(session, repo_id)
    return _file_trend_to_dict(file_trend(snapshots, file_path))


@router.get("/api/repos/{repo_id}/health/trend", response_model=HealthTrendResponse)
async def health_trend(
    repo_id: str,
    limit: int = Query(20, ge=1, le=50),
    session: AsyncSession = Depends(get_db_session),
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
            file_deltas.append({"file_path": p, "before": before, "after": after, "delta": d})
        # Largest movement first, in either direction. Sorting ascending and
        # slicing made the block the N biggest *drops*: improvements only
        # survived the cut while fewer than ``FILE_DELTA_LIMIT`` files had
        # regressed, so the one render that most wants to show progress — the
        # index after a cleanup — is the one that hides it. The client
        # (``TrendSlopeChart``) already ranks by absolute delta before drawing,
        # so this is also the order it was asking for.
        file_deltas.sort(key=lambda r: (-abs(r["delta"]), r["file_path"]))

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
        "file_deltas": file_deltas[:FILE_DELTA_LIMIT],
        # The true count behind the slice, so the UI can say "N of M" instead
        # of presenting a truncated list as the whole story.
        "file_deltas_total": len(file_deltas),
        "snapshot_count": len(snapshots),
    }
