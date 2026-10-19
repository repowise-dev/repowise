"""Area response models: directory rollups at any depth.

The repository root is the area ``""``. Every figure covers the area's scored
files (a file health has no dialect for is in none), complete over the
repository rather than capped like the map's drawn field.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .coupling import CouplingAreaSummary

Band = Literal["at_risk", "needs_work", "fair", "good", "excellent"]


class AreaOwner(BaseModel):
    #: Identity key, as the contributors pages key people.
    key: str
    name: str
    kind: Literal["human", "agent", "bot"]
    #: Files in the area this identity is primary owner of.
    files: int
    #: ``files`` over the area's file count, 0-1.
    share: float


class AreaBandCounts(BaseModel):
    at_risk: int
    needs_work: int
    fair: int
    good: int
    excellent: int


class AreaSeverityCounts(BaseModel):
    critical: int
    high: int
    medium: int
    low: int


class AreaWorstFile(BaseModel):
    path: str | None
    score: float


class AreaNode(BaseModel):
    path: str
    #: Last path segment; ``""`` for the root.
    name: str
    #: Path segments; the root is 0.
    depth: int
    parent: str | None
    has_children: bool
    #: Files directly in this directory, not in a subdirectory.
    direct_file_count: int
    file_count: int
    nloc: int
    #: NLOC-weighted average score, the module-health fold.
    health: float
    band: Band
    band_counts: AreaBandCounts
    worst_file: AreaWorstFile
    hotspot_count: int
    #: Files whose bus factor is exactly 1.
    bus_factor_1_count: int
    #: The person owning the most files; automation never leads an area.
    primary_owner: AreaOwner | None
    #: NLOC-weighted line coverage over measured files; ``None`` with no report.
    line_coverage_pct: float | None
    coverage_file_count: int
    #: Open findings per severity, advisory left out.
    findings: AreaSeverityCounts
    #: Score at the newest snapshot minus at the snapshot nearest 30 days
    #: before it (see ``delta_window``); ``None`` when either lacks the area.
    health_delta_30d: float | None


class AreaDeltaWindow(BaseModel):
    """The snapshots behind ``health_delta_30d``; the baseline may be nearer than 30 days."""

    target_days: int
    latest_at: str | None
    baseline_at: str | None


class AreaHeld(BaseModel):
    """The origins the map holds out by default, and what this answer left out."""

    #: ``MAP_HELD_ORIGINS``, in order: what ``hold=map`` leaves out.
    default: list[str]
    #: Origins left out of every figure in this answer; empty with ``hold=none``.
    excluded: list[str]
    #: Files per held origin, repository-wide, for a toggle's count.
    counts: dict[str, int]


class AreaTreeResponse(BaseModel):
    root: AreaNode
    depth: int
    #: Every area up to ``depth`` levels below the root, sorted by path.
    nodes: list[AreaNode]
    delta_window: AreaDeltaWindow
    #: Absent on older servers.
    held: AreaHeld | None = None


class AreaBandShare(BaseModel):
    files: int
    nloc: int
    #: NLOC-weighted share of the area, 0-100.
    pct: float


class AreaBandShares(BaseModel):
    at_risk: AreaBandShare
    needs_work: AreaBandShare
    fair: AreaBandShare
    good: AreaBandShare
    excellent: AreaBandShare


class AreaDistribution(BaseModel):
    total_files: int
    total_nloc: int
    bands: AreaBandShares


class AreaFileRow(BaseModel):
    path: str
    score: float
    band: Band
    nloc: int


class AreaHotspots(BaseModel):
    total: int
    files: list[AreaFileRow]


class AreaOwners(BaseModel):
    total: int
    top: list[AreaOwner]


class AreaTrendPoint(BaseModel):
    taken_at: str | None
    health: float


class AreaFinding(BaseModel):
    id: str
    file_path: str
    biomarker_type: str
    severity: str
    function_name: str | None
    line_start: int | None
    health_impact: float | None
    reason: str | None


class AreaTestReach(BaseModel):
    files: int
    #: Files with a paired test file.
    with_test_file: int
    measured_files: int
    line_coverage_pct: float | None


class AreaDetailResponse(BaseModel):
    area: AreaNode
    distribution: AreaDistribution
    #: Worst-first, bounded.
    worst_files: list[AreaFileRow]
    hotspots: AreaHotspots
    owners: AreaOwners
    #: Area score per snapshot, oldest first, over the newest 20 snapshots.
    trend: list[AreaTrendPoint]
    #: First open findings in the finding lists' priority order.
    top_findings: list[AreaFinding]
    test_reach: AreaTestReach
    delta_window: AreaDeltaWindow
    #: Areas outside this one its files change with, strongest first; test
    #: material left out. Each named at this area's own depth.
    coupled_areas: list[CouplingAreaSummary] = Field(default_factory=list)
