"""Area health over the recorded snapshot history.

A snapshot stores ``{path: score}`` and no line counts, so an area's score at a
snapshot is weighted by the files' *current* NLOC (1 for a file that no longer
exists). Both ends of a delta use the same weights, so the delta moves only
when scores move, never because the weighting did. That is close to, not the
same number as, the live area health, which the current rows weight exactly.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from repowise.core.analysis.health.rows import field

from .paths import ancestors, in_area

__all__ = [
    "DELTA_WINDOW_DAYS",
    "area_score",
    "area_scores",
    "attach_deltas",
    "baseline_index",
    "nloc_weights",
]

DELTA_WINDOW_DAYS = 30


def nloc_weights(metrics: Sequence[Any]) -> dict[str, int]:
    """``{path: weight}`` with the floor of 1 every health average uses."""
    return {field(m, "file_path"): max(int(field(m, "nloc", 0) or 0), 1) for m in metrics}


def area_scores(score_map: Mapping[str, float], weights: Mapping[str, int]) -> dict[str, float]:
    """Every area's weighted score in one snapshot map."""
    sums: dict[str, float] = defaultdict(float)
    totals: dict[str, int] = defaultdict(int)
    for path, score in score_map.items():
        if score is None:
            continue
        weight = weights.get(path, 1)
        for area in ancestors(path):
            sums[area] += float(score) * weight
            totals[area] += weight
    return {area: round(sums[area] / totals[area], 2) for area in sums}


def area_score(
    score_map: Mapping[str, float], area: str, weights: Mapping[str, int]
) -> float | None:
    """One area's weighted score in one snapshot map, ``None`` when it held no file."""
    total = weight_sum = 0.0
    for path, score in score_map.items():
        if score is None or not in_area(path, area):
            continue
        weight = weights.get(path, 1)
        total += float(score) * weight
        weight_sum += weight
    return round(total / weight_sum, 2) if weight_sum else None


def baseline_index(
    taken: Sequence[datetime | None], window_days: int = DELTA_WINDOW_DAYS
) -> int | None:
    """The snapshot nearest *window_days* before the newest one, never the newest.

    *taken* is oldest-first. ``None`` with fewer than two dated snapshots. The
    chosen one may sit nearer than the window when the history is short; the
    caller reports its date so the reading names its real span.
    """
    if len(taken) < 2 or taken[-1] is None:
        return None
    target = taken[-1] - timedelta(days=window_days)
    dated = [(i, t) for i, t in enumerate(taken[:-1]) if t is not None]
    if not dated:
        return None
    return min(dated, key=lambda item: (abs(item[1] - target), -item[0]))[0]


def attach_deltas(
    nodes: Mapping[str, dict[str, Any]],
    latest: Mapping[str, float],
    baseline: Mapping[str, float],
) -> None:
    """Set each node's ``health_delta_30d``; left ``None`` where either end lacks the area."""
    for area, node in nodes.items():
        if area in latest and area in baseline:
            node["health_delta_30d"] = round(latest[area] - baseline[area], 2)
