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


@dataclass
class TestCoverage:
    """One ``(test, file, lines)`` fact — the per-test coverage substrate.

    Where :class:`FileCoverage` collapses every test into one hit-wins
    aggregate, this keeps the *test dimension*: a record says "test
    ``test_id`` covered ``covered_lines`` of ``file_path``". It is the raw
    material for the test-to-code map (run-only-affected-tests, real
    ``missing_tests``, etc.) and is produced only when a report actually
    carries contexts.

    ``test_id`` is the report's raw test identifier (coverage.py context
    ``module::qualname|phase`` or an lcov ``TN:`` name). ``file_path`` is the
    raw source path from the report until :func:`resolve_test_reports`
    rewrites it to a canonical repo key. ``test_file`` is the canonical key
    of the test's *own* source file, filled in best-effort by the resolver
    (``None`` when the test id is not path-shaped or does not resolve).
    """

    test_id: str
    file_path: str
    covered_lines: list[int] = field(default_factory=list)
    source_format: str = "unknown"
    test_file: str | None = None


@dataclass
class ContextCoverageReport:
    """Per-test records parsed from one context-carrying report.

    ``has_contexts`` is the loud-degradation signal: a report run *without*
    contexts (a plain lcov, or ``coverage run`` with no ``--contexts``)
    parses to ``records=[]`` and ``has_contexts=False`` so callers can say
    "aggregate only, per-test features unavailable" instead of silently
    emitting nothing.
    """

    source_format: str
    records: list[TestCoverage] = field(default_factory=list)
    has_contexts: bool = False
