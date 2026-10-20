"""One area's card: its rollup plus the bounded lists a side panel opens on."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from repowise.core.analysis.health.grading import band_for, distribution
from repowise.core.analysis.health.ranking import sort_metrics_worst_first
from repowise.core.analysis.health.rows import field
from repowise.core.analysis.health.scoring import is_advisory

from .owners import owner_breakdown
from .paths import in_area
from .rollup import AreaIndex, coverage_rollup

__all__ = ["DETAIL_LIMIT", "OWNER_LIMIT", "area_detail"]

#: Rows per list (worst files, hotspots, findings). A card, not a table.
DETAIL_LIMIT = 8
OWNER_LIMIT = 5


def _file(row: Any) -> dict[str, Any]:
    score = float(field(row, "score"))
    return {
        "path": field(row, "file_path"),
        "score": round(score, 2),
        "band": band_for(score),
        "nloc": int(field(row, "nloc", 0) or 0),
    }


def _top_findings(index: AreaIndex, area: str, limit: int) -> list[dict[str, Any]]:
    """The first *limit* open findings in the area, in the finding lists' order.

    Advisory findings are left out, as from the severity counts beside them.
    """
    out: list[dict[str, Any]] = []
    for finding in index.findings:
        if len(out) >= limit:
            break
        if in_area(finding["file_path"], area) and not is_advisory(
            finding["biomarker_type"] or ""
        ):
            out.append(finding)
    return out


def _is_hotspot(index: AreaIndex, path: str) -> bool:
    return bool(index.git_by_path.get(path, {}).get("is_hotspot"))


def _test_reach(group: Sequence[Any]) -> dict[str, Any]:
    """Paired test files, and measured line coverage where a report covered the area."""
    coverage, measured = coverage_rollup(group)
    return {
        "files": len(group),
        "with_test_file": sum(1 for r in group if field(r, "has_test_file")),
        "measured_files": measured,
        "line_coverage_pct": coverage,
    }


def area_detail(
    index: AreaIndex,
    area: str,
    *,
    trend: Sequence[dict[str, Any]] = (),
    coupled: Sequence[dict[str, Any]] = (),
    limit: int = DETAIL_LIMIT,
) -> dict[str, Any] | None:
    """The area's rollup and lists, or ``None`` when no scored file is under it.

    *trend* is the area's score per snapshot (``[{taken_at, health}]``), read
    from the history by the caller. *coupled* is the areas it changes with
    (:func:`repowise.core.analysis.coupling.coupled_areas`), folded by the
    caller from the co-change rows this index does not read.
    """
    node = index.nodes.get(area)
    if node is None:
        return None
    group = index.members[area]
    ranked = sort_metrics_worst_first(group, index.deductions)
    hotspots = [r for r in ranked if _is_hotspot(index, field(r, "file_path"))]
    owners = owner_breakdown((field(r, "file_path") for r in group), index.owners, len(group))
    return {
        "area": node,
        "distribution": distribution(group),
        "worst_files": [_file(r) for r in ranked[:limit]],
        "hotspots": {"total": len(hotspots), "files": [_file(r) for r in hotspots[:limit]]},
        "owners": {"total": len(owners), "top": owners[:OWNER_LIMIT]},
        "trend": list(trend),
        "top_findings": _top_findings(index, area, limit),
        "test_reach": _test_reach(group),
        "coupled_areas": list(coupled),
    }
