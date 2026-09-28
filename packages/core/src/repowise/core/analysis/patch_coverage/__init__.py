"""Patch coverage: the share of a change's executable lines the tests ran."""

from __future__ import annotations

from .compute import (
    FilePatchCoverage,
    FileStatus,
    GateStatus,
    PatchCoverage,
    PatchScope,
    compute_patch_coverage,
    line_ranges,
)
from .render import github_annotations, headline, render_markdown

__all__ = [
    "FilePatchCoverage",
    "FileStatus",
    "GateStatus",
    "PatchCoverage",
    "PatchScope",
    "compute_patch_coverage",
    "github_annotations",
    "headline",
    "line_ranges",
    "render_markdown",
]
