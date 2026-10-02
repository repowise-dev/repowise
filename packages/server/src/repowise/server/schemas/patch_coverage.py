"""Patch coverage over the wire: ``PatchCoverage.to_dict()``, typed.

Mirrors ``repowise coverage check --format json`` field for field, so a client
reading this endpoint and a CI job reading the command see one shape.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from repowise.core.analysis.health.coverage.freshness import FreshnessStatus
from repowise.core.analysis.patch_coverage.compute import FileStatus, GateStatus


class _Strict(BaseModel):
    # A key ``to_dict`` gains must fail validation, not vanish from the API.
    model_config = ConfigDict(extra="forbid")


class PatchCoverageFile(_Strict):
    file_path: str
    status: FileStatus
    changed_line_count: int
    coverable_line_count: int
    covered_line_count: int
    #: 0-100, floored to two decimals; null when no changed line is executable.
    patch_coverage_pct: float | None
    #: Inclusive ``[start, end]`` runs of changed, executable, unexecuted lines.
    uncovered_ranges: list[list[int]]


class PatchCoverageFileCounts(_Strict):
    measured: int
    not_in_report: int
    no_line_data: int
    no_coverable_changes: int
    out_of_scope: int


class PatchCoverageScope(_Strict):
    label: str
    source_formats: list[str]
    reports: list[str]
    #: Null when unknown: stored by an ingest that did not record its path counts.
    report_path_count: int | None
    unmatched_report_path_count: int | None
    #: Fewer than half the report's paths matched this repository.
    mapping_partial: bool
    measured_commit: str | None
    #: ``stale`` when the coverage was measured at another commit than the
    #: change's head: the figure still computes but describes other code.
    freshness: FreshnessStatus


class PatchCoverageResponse(_Strict):
    patch_coverage_pct: float | None
    covered_line_count: int
    coverable_line_count: int
    threshold: float | None
    gate: GateStatus
    file_counts: PatchCoverageFileCounts
    files: list[PatchCoverageFile]
    scope: PatchCoverageScope
