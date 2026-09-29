"""Patch coverage: the share of a change's executable lines the tests ran."""

from __future__ import annotations

from .compute import (
    FilePatchCoverage,
    FileStatus,
    GateStatus,
    PatchCoverage,
    PatchScope,
    PathGateResult,
    compute_patch_coverage,
    patch_coverage_from_resolved,
)
from .render import (
    RANGE_LIMIT,
    STATUS_TEXT,
    attention_rows,
    fmt_pct,
    format_ranges,
    github_annotations,
    headline,
    path_gate_row,
    path_gate_verdict,
    render_markdown,
    scope_line,
)
from .stored import stored_patch_coverage

__all__ = [
    "RANGE_LIMIT",
    "STATUS_TEXT",
    "FilePatchCoverage",
    "FileStatus",
    "GateStatus",
    "PatchCoverage",
    "PatchScope",
    "PathGateResult",
    "attention_rows",
    "compute_patch_coverage",
    "fmt_pct",
    "format_ranges",
    "github_annotations",
    "headline",
    "patch_coverage_from_resolved",
    "path_gate_row",
    "path_gate_verdict",
    "render_markdown",
    "scope_line",
    "stored_patch_coverage",
]
