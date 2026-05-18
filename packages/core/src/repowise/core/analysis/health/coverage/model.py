"""Dataclasses produced by coverage parsers.

Parsers return a :class:`CoverageReport` containing one
:class:`FileCoverage` per source file referenced in the report. The
engine and persistence layer consume these dataclasses; the ORM
``CoverageFile`` row is written from them in
``persistence.crud.save_coverage_files``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FileCoverage:
    """Per-file coverage extracted from a single report."""

    file_path: str
    line_coverage_pct: float
    branch_coverage_pct: float | None
    covered_lines: list[int] = field(default_factory=list)
    total_coverable_lines: int = 0


@dataclass
class CoverageReport:
    """Whole-report bundle returned by every parser.

    ``source_format`` is one of ``lcov`` / ``cobertura`` / ``clover``.
    ``commit_sha`` is best-effort: parsers leave it ``None``; the CLI
    fills it in from ``git rev-parse HEAD`` when available.
    """

    source_format: str
    files: list[FileCoverage] = field(default_factory=list)
    commit_sha: str | None = None
