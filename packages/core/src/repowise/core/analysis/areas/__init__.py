"""Areas: any directory at any depth, rolled up from the analyzers' stored outputs.

The unit the zoomable map, its side panel, contributors and coupling views
share. Reads health, git and finding rows; never re-scores anything.
"""

from __future__ import annotations

from .depth import choose_depth, group_counts
from .detail import DETAIL_LIMIT, area_detail
from .owners import FileOwner, file_owners
from .paths import ROOT_AREA, ancestors, area_at, area_depth, in_area, normalize_area
from .rollup import AreaIndex, build_area_index
from .trend import area_score, area_scores, attach_deltas, baseline_index, nloc_weights

__all__ = [
    "DETAIL_LIMIT",
    "ROOT_AREA",
    "AreaIndex",
    "FileOwner",
    "ancestors",
    "area_at",
    "area_depth",
    "area_detail",
    "area_score",
    "area_scores",
    "attach_deltas",
    "baseline_index",
    "build_area_index",
    "choose_depth",
    "file_owners",
    "group_counts",
    "in_area",
    "nloc_weights",
    "normalize_area",
]
