"""Directory rollups at every depth, folded once over the whole repository.

Every scored file is added to each area above it, then each area is folded with
:func:`..health.aggregation.group_rollup`, the fold module health uses, so an
area's health means exactly what a module's does: the NLOC-weighted average of
its scored files, worst performer under the shared ranking key. A file health
cannot score (no dialect for its language) is in no figure, as in
``module_rollups``.

Pure: the caller loads the rows and keeps the result per index state.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from repowise.core.analysis.health.aggregation import group_rollup, severity_breakdown
from repowise.core.analysis.health.grading import BAND_ORDER, band_for, distribution
from repowise.core.analysis.health.rows import field as row_field
from repowise.core.analysis.health.rows import scored_rows

from .owners import FileOwner, owner_breakdown, primary_owner
from .paths import ancestors, area_depth, area_name, area_parent

__all__ = [
    "AreaIndex",
    "build_area_index",
    "coverage_rollup",
    "finding_row",
    "severity_by_file",
]

#: The fields an area reads of each row; the index is kept in memory per index
#: state, so the rest of a row (an ORM entity is a few KB) is not kept.
_METRIC_FIELDS = ("file_path", "score", "nloc", "is_test", "line_coverage_pct", "has_test_file")
_GIT_FIELDS = ("is_hotspot", "bus_factor")


def _pick(row: Any, names: tuple[str, ...]) -> dict[str, Any]:
    return {name: row_field(row, name) for name in names}


@dataclass
class AreaIndex:
    """Every area's rollup plus what a detail view folds further."""

    nodes: dict[str, dict[str, Any]]
    #: Scored metric rows (the fields an area reads) under each area, at any depth.
    members: dict[str, list[Any]]
    children: dict[str, list[str]]
    #: ``{path: {is_hotspot, bus_factor}}``: only what an area reads, since
    #: the index is kept in memory per index state.
    git_by_path: Mapping[str, dict[str, Any]] = field(default_factory=dict)
    owners: Mapping[str, FileOwner] = field(default_factory=dict)
    deductions: Mapping[str, float] = field(default_factory=dict)
    #: Open findings as :func:`finding_row` dicts, in the finding lists' priority order.
    findings: Sequence[dict[str, Any]] = ()


def finding_row(row: Any) -> dict[str, Any]:
    """The fields an area shows of one finding, detached from its stored row."""
    impact = row_field(row, "health_impact")
    return {
        "id": row_field(row, "id"),
        "file_path": row_field(row, "file_path"),
        "biomarker_type": row_field(row, "biomarker_type"),
        "severity": str(row_field(row, "severity") or "").lower(),
        "function_name": row_field(row, "function_name"),
        "line_start": row_field(row, "line_start"),
        "health_impact": round(float(impact), 3) if impact is not None else None,
        "reason": row_field(row, "reason"),
    }


def severity_by_file(findings: Iterable[Any]) -> dict[str, dict[str, int]]:
    """Open-finding counts per declared severity, per file (advisory left out)."""
    grouped: dict[str, list[Any]] = defaultdict(list)
    for finding in findings:
        grouped[row_field(finding, "file_path")].append(finding)
    return {path: severity_breakdown(rows) for path, rows in grouped.items()}


def coverage_rollup(group: Sequence[Any]) -> tuple[float | None, int]:
    """NLOC-weighted line coverage over the files a report measured, and how many.

    ``None`` when no file in the group was measured: no report is not 0%.
    """
    measured = [r for r in group if row_field(r, "line_coverage_pct") is not None]
    if not measured:
        return None, 0
    weighted = [(max(int(row_field(r, "nloc", 0) or 0), 1), r) for r in measured]
    total = sum(w * float(row_field(r, "line_coverage_pct")) for w, r in weighted)
    return round(total / sum(w for w, _ in weighted), 2), len(measured)


def _sum_severities(
    paths: Iterable[str], severities: Mapping[str, dict[str, int]]
) -> dict[str, int]:
    out = dict.fromkeys(severity_breakdown(()), 0)
    for path in paths:
        for severity, count in severities.get(path, {}).items():
            out[severity] += count
    return out


def _node(
    area: str,
    group: list[Any],
    *,
    direct: int,
    has_children: bool,
    index: AreaIndex,
    severities: Mapping[str, dict[str, int]],
) -> dict[str, Any]:
    rollup = group_rollup(group, index.deductions)
    paths = [row_field(r, "file_path") for r in group]
    git = [index.git_by_path[p] for p in paths if p in index.git_by_path]
    bands = distribution(group)["bands"]
    coverage, measured = coverage_rollup(group)
    return {
        "path": area,
        "name": area_name(area),
        "depth": area_depth(area),
        "parent": area_parent(area),
        "has_children": has_children,
        "direct_file_count": direct,
        "file_count": rollup["file_count"],
        "nloc": rollup["nloc"],
        "health": rollup["average_health"],
        "band": band_for(rollup["average_health"]),
        "band_counts": {band: bands[band]["files"] for band in BAND_ORDER},
        "worst_file": {
            "path": rollup["worst_performer_path"],
            "score": rollup["worst_performer_score"],
        },
        "hotspot_count": sum(1 for g in git if row_field(g, "is_hotspot")),
        "bus_factor_1_count": sum(1 for g in git if row_field(g, "bus_factor") == 1),
        "primary_owner": primary_owner(owner_breakdown(paths, index.owners, len(paths))),
        "line_coverage_pct": coverage,
        "coverage_file_count": measured,
        "findings": _sum_severities(paths, severities),
        # Filled from the snapshot history by :func:`..trend.attach_deltas`.
        "health_delta_30d": None,
    }


def build_area_index(
    metrics: Iterable[Any],
    *,
    git_rows: Iterable[Any] = (),
    owners: Mapping[str, FileOwner] | None = None,
    findings: Iterable[Any] = (),
    deductions: Mapping[str, float] | None = None,
) -> AreaIndex:
    """Fold every scored file into each directory above it, then roll each one up.

    *findings* are the open findings the finding lists show; *deductions* the
    per-file pre-clamp magnitudes the worst-first ranking breaks ties on.
    """
    members: dict[str, list[Any]] = defaultdict(list)
    direct: dict[str, int] = defaultdict(int)
    children: dict[str, set[str]] = defaultdict(set)
    for row in (_pick(m, _METRIC_FIELDS) for m in scored_rows(metrics)):
        chain = ancestors(row_field(row, "file_path"))
        for area in chain:
            members[area].append(row)
        direct[chain[-1]] += 1
        for parent, child in pairwise(chain):
            children[parent].add(child)

    index = AreaIndex(
        nodes={},
        members=dict(members),
        children={area: sorted(kids) for area, kids in children.items()},
        git_by_path={
            row_field(g, "file_path"): _pick(g, _GIT_FIELDS) for g in git_rows
        },
        owners=owners or {},
        deductions=deductions or {},
    )
    index.findings = [finding_row(f) for f in findings]
    severities = severity_by_file(index.findings)
    for area, group in index.members.items():
        index.nodes[area] = _node(
            area,
            group,
            direct=direct.get(area, 0),
            has_children=bool(index.children.get(area)),
            index=index,
            severities=severities,
        )
    return index
