"""File-level + repo-level health trend routes."""

from __future__ import annotations

import json
from typing import Any

from fastapi import Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.scope import parse_scope
from repowise.core.analysis.health.trends import (
    diff_snapshots,
    drop_unscoped_fields,
    file_trend,
    project_scope,
    recent_kpis,
)
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session
from repowise.server.schemas import FileHealthTrendResponse, HealthTrendResponse

from ._router import router
from .scope import ScopeQuery
from .serializers import _file_trend_to_dict

# How many per-file movements the trend response carries. Paired with
# ``file_deltas_total`` so a caller can always tell a full list from a slice.
FILE_DELTA_LIMIT: int = 50


async def _score_map(session: AsyncSession, snap: Any) -> dict:
    """One snapshot's ``{path: score}`` map, loaded now because the list deferred it."""
    row = getattr(snap, "source", None) or snap  # a scoped projection reads through
    await session.refresh(row, ["per_file_scores_json"])
    return json.loads(row.per_file_scores_json or "{}")


def _by_size(row: dict) -> tuple[float, str]:
    return (-abs(row["delta"]), row["file_path"])


def _slice_both_ways(deltas: list[dict]) -> list[dict]:
    """The largest ``FILE_DELTA_LIMIT`` movements, with half the slice held for
    each direction while that direction has rows to fill it.

    The page lists "got worse" and "improved" as two groups, and a plain top-N
    by size let a wave of small regressions leave the improved group empty
    beside a count saying it has rows. Either direction still takes the whole
    slice when the other has too few to use its half. Largest first.
    """
    worse = [d for d in deltas if d["delta"] < 0]
    improved = [d for d in deltas if d["delta"] > 0]
    half = FILE_DELTA_LIMIT // 2
    keep_worse = min(len(worse), max(half, FILE_DELTA_LIMIT - len(improved)))
    keep_improved = min(len(improved), FILE_DELTA_LIMIT - keep_worse)
    return sorted(worse[:keep_worse] + improved[:keep_improved], key=_by_size)


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
    scope: str = ScopeQuery,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    narrowed = parse_scope(scope) == "production"
    # Every row's scalars, but only the last two rows' per-file maps: each map
    # is sized by the repository, and the movement list is all that reads them.
    snapshots = project_scope(
        await crud.list_health_snapshots(session, repo_id, file_maps=False), scope
    )
    summary = diff_snapshots(snapshots)
    # Snapshots record per-file scores for the whole repository, so a narrowed
    # response has to drop the test files from the movement list too.
    test_paths = await crud.get_test_file_paths(session, repo_id) if narrowed else set()

    # Per-file delta from the last two snapshots.
    file_deltas: list[dict] = []
    if len(snapshots) >= 2:
        try:
            prev = await _score_map(session, snapshots[-2])
            cur = await _score_map(session, snapshots[-1])
        except Exception:
            prev, cur = {}, {}
        all_paths = set(prev) | set(cur)
        for p in all_paths:
            before = prev.get(p)
            after = cur.get(p)
            if before is None or after is None or p in test_paths:
                continue
            d = round(float(after) - float(before), 2)
            if d == 0:
                continue
            file_deltas.append({"file_path": p, "before": before, "after": after, "delta": d})
        # Largest movement first, in either direction. Sorting ascending and
        # slicing made the block the N biggest *drops*: improvements only
        # survived the cut while fewer than ``FILE_DELTA_LIMIT`` files had
        # regressed, so the one render that most wants to show progress — the
        # index after a cleanup — is the one that hides it.
        file_deltas.sort(key=_by_size)

    return {
        "history": (
            drop_unscoped_fields(recent_kpis(snapshots, limit=limit, doc_drift=True))
            if narrowed
            else recent_kpis(snapshots, limit=limit, doc_drift=True)
        ),
        "summary": {
            "current_hotspot_health": None if narrowed else summary.current_hotspot_health,
            "current_average_health": summary.current_average_health,
            "previous_hotspot_health": None if narrowed else summary.previous_hotspot_health,
            "previous_average_health": summary.previous_average_health,
            "hotspot_delta": None if narrowed else summary.hotspot_delta,
            "average_delta": summary.average_delta,
            "current_structure_deduction": summary.current_structure_deduction,
            "current_history_deduction": summary.current_history_deduction,
        },
        "alerts": [
            {
                "kind": a.kind,
                "metric": a.metric,
                "current": a.current,
                "baseline": a.baseline,
                "delta": a.delta,
                "message": a.message,
                "driver": a.driver,
                "structure_delta": a.structure_delta,
                "history_delta": a.history_delta,
            }
            for a in summary.alerts
        ],
        "file_deltas": _slice_both_ways(file_deltas),
        # The true count behind the slice, so the UI can say "N of M" instead
        # of presenting a truncated list as the whole story.
        "file_deltas_total": len(file_deltas),
        # Each direction's true count, so a "got worse" group can say "N of M".
        "file_deltas_worse": sum(1 for d in file_deltas if d["delta"] < 0),
        "file_deltas_improved": sum(1 for d in file_deltas if d["delta"] > 0),
        "snapshot_count": len(snapshots),
        "scope": scope,
    }
