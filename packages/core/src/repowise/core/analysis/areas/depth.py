"""How deep to group a set of files so the grouping says something.

A queue of causes grouped one level too shallow is one card holding
everything; one level too deep is a card per file. The chosen depth is the
shallowest that splits the files into a few areas with none holding most of
them, so the first screen already says where the work concentrates.
"""

from __future__ import annotations

from collections.abc import Mapping

from .paths import ROOT_AREA, area_at, area_depth

__all__ = ["MAX_SHARE", "MIN_AREAS", "SMALL", "choose_depth", "group_counts"]

MIN_AREAS = 3
"""Fewer areas than this is barely a split."""

SMALL = 10
"""Below this much weight two areas already say where it sits."""

MAX_SHARE = 0.5
"""An area holding more than this share of the weight is most of the answer."""

_MAX_EXTRA_DEPTH = 8


def group_counts(weights: Mapping[str, int], depth: int) -> dict[str, int]:
    """*weights* (file path to a count) summed per area at *depth*."""
    grouped: dict[str, int] = {}
    for path, weight in weights.items():
        area = area_at(path, depth)
        grouped[area] = grouped.get(area, 0) + weight
    return grouped


def choose_depth(weights: Mapping[str, int], *, base: str = ROOT_AREA) -> int:
    """The shallowest depth below *base* that splits *weights* into at least
    ``MIN_AREAS`` areas (two below ``SMALL``) with none above ``MAX_SHARE`` of
    the total; failing
    that, the depth whose largest area is smallest, the shallower on a tie.
    """
    start = area_depth(base) + 1
    total = sum(weights.values())
    if total == 0:
        return start
    best, best_share = start, 2.0
    # Past the deepest directory, every depth groups the same way.
    deepest = max(path.count("/") for path in weights)
    for depth in range(start, max(start, min(deepest, start + _MAX_EXTRA_DEPTH - 1)) + 1):
        grouped = group_counts(weights, depth)
        share = max(grouped.values()) / total
        if len(grouped) >= (MIN_AREAS if total >= SMALL else 2) and share <= MAX_SHARE:
            return depth
        if share < best_share:
            best, best_share = depth, share
    return best
