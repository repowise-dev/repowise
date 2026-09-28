"""Patch coverage over the wire: ``PatchCoverage.to_dict()``, typed.

Mirrors ``repowise coverage check --format json`` field for field, so a client
reading this endpoint and a CI job reading the command see one shape.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class PatchCoverageFile(BaseModel):
    file_path: str
    status: Literal["measured", "not_in_report", "no_line_data", "no_coverable_changes"]
    changed_line_count: int
    coverable_line_count: int
    covered_line_count: int
    #: 0-100, floored to two decimals; null when no changed line is executable.
    patch_coverage_pct: float | None
    #: Inclusive ``[start, end]`` runs of changed, executable, unexecuted lines.
    uncovered_ranges: list[list[int]]


class PatchCoverageFileCounts(BaseModel):
    measured: int
    not_in_report: int
    no_line_data: int
    no_coverable_changes: int
    out_of_scope: int


class PatchCoverageScope(BaseModel):
    label: str
    source_formats: list[str]
    reports: list[str]
    report_path_count: int
    unmatched_report_path_count: int
    measured_commit: str | None
    #: ``stale`` when the coverage was measured at another commit than the
    #: change's head: the figure still computes but describes other code.
    freshness: Literal["current", "stale", "unknown"]


class PatchCoverageResponse(BaseModel):
    patch_coverage_pct: float | None
    covered_line_count: int
    coverable_line_count: int
    threshold: float | None
    gate: Literal["pass", "fail", "no_data", "not_set"]
    file_counts: PatchCoverageFileCounts
    files: list[PatchCoverageFile]
    scope: PatchCoverageScope
