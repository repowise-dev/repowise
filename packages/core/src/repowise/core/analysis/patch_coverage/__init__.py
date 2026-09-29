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
    risk_basis_line,
    risk_words,
    risky_line,
    scope_line,
)
from .risk import (
    FileRisk,
    GitFixHistory,
    IndexFacts,
    assess_risks,
    attach_risk,
    read_git_fix_history,
    risk_unreadable,
)
from .stored import read_index_facts, stored_patch_coverage

__all__ = [
    "RANGE_LIMIT",
    "STATUS_TEXT",
    "FilePatchCoverage",
    "FileRisk",
    "FileStatus",
    "GateStatus",
    "GitFixHistory",
    "IndexFacts",
    "PatchCoverage",
    "PatchScope",
    "PathGateResult",
    "assess_risks",
    "attach_risk",
    "attention_rows",
    "compute_patch_coverage",
    "fmt_pct",
    "format_ranges",
    "github_annotations",
    "headline",
    "patch_coverage_from_resolved",
    "path_gate_row",
    "path_gate_verdict",
    "read_git_fix_history",
    "read_index_facts",
    "render_markdown",
    "risk_basis_line",
    "risk_unreadable",
    "risk_words",
    "risky_line",
    "scope_line",
    "stored_patch_coverage",
]
