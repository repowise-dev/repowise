"""How deep to group a set of files so the grouping says something.

A view grouped one level too shallow is one area holding everything; one
level too deep is an area per file. The chosen depth is the shallowest that
splits the files into enough areas with none holding most of them. The
performance queue and the coupling grid both choose their depth here, with
their own thresholds.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .paths import ROOT_AREA, area_at, area_depth, in_area

__all__ = ["MAX_SHARE", "MIN_AREAS", "SMALL", "choose_depth", "group_counts"]

MIN_AREAS = 3
"""Fewer areas than this is barely a split."""

MAX_SHARE = 0.5
"""An area holding more than this share of the weight is most of the answer."""

SMALL = 10
"""Below this much weight two areas already say where it sits."""

MAX_EXTRA_DEPTH = 8
"""How many levels below the base the search looks."""


def _weights(paths: Mapping[str, int] | Iterable[str]) -> dict[str, int]:
    if isinstance(paths, Mapping):
        return {path: int(weight) for path, weight in paths.items()}
    weights: dict[str, int] = {}
    for path in paths:
        weights[path] = weights.get(path, 0) + 1
    return weights


def group_counts(weights: Mapping[str, int], depth: int) -> dict[str, int]:
    """*weights* (file path to a count) summed per area at *depth*."""
    grouped: dict[str, int] = {}
    for path, weight in weights.items():
        area = area_at(path, depth)
        grouped[area] = grouped.get(area, 0) + weight
    return grouped


def choose_depth(
    paths: Mapping[str, int] | Iterable[str],
    *,
    base: str = ROOT_AREA,
    min_areas: int = MIN_AREAS,
    max_share: float = MAX_SHARE,
    small: int = SMALL,
    max_depth: int = MAX_EXTRA_DEPTH,
) -> int:
    """The absolute depth to group *paths* at below *base*.

    *paths* are file paths (each weighing one) or a mapping of path to
    weight; only those inside *base* count. The answer is the shallowest
    depth, at most *max_depth* levels below *base*, that splits them into at
    least *min_areas* areas (two when the total weight is under *small*)
    with none above *max_share* of the weight. Failing that, it is the depth
    whose largest area is smallest, the shallower on a tie, since going
    deeper without splitting adds nothing.
    """
    weights = {p: w for p, w in _weights(paths).items() if in_area(p, base)}
    start = area_depth(base) + 1
    total = sum(weights.values())
    if total == 0:
        return start
    needed = min_areas if total >= small else min(min_areas, 2)
    # Past the deepest directory, every depth groups the same way.
    deepest = max(path.count("/") for path in weights)
    best, best_share = start, 2.0
    for depth in range(start, max(start, min(deepest, start + max_depth - 1)) + 1):
        grouped = group_counts(weights, depth)
        share = max(grouped.values()) / total
        if len(grouped) >= needed and share <= max_share:
            return depth
        if share < best_share:
            best, best_share = depth, share
    return best
