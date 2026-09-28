"""Dataclasses produced by coverage parsers.

Parsers return a :class:`CoverageReport` containing one
:class:`FileCoverage` per source file referenced in the report. The
engine and persistence layer consume these dataclasses; the ORM
``CoverageFile`` row is written from them in
``persistence.crud.save_coverage_files``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET


@dataclass
class FileCoverage:
    """Per-file coverage extracted from a single report.

    ``coverable_lines`` is the set of lines the report calls executable
    (hit or not). It is what tells an uncovered changed line apart from a
    changed comment or blank line, so patch coverage needs it. Empty means
    the report did not say which lines are executable, not that none are.
    """

    file_path: str
    line_coverage_pct: float
    branch_coverage_pct: float | None
    covered_lines: list[int] = field(default_factory=list)
    total_coverable_lines: int = 0
    coverable_lines: list[int] = field(default_factory=list)


def file_coverage(
    file_path: str,
    covered: Iterable[int],
    coverable: Iterable[int],
    *,
    branches_found: int = 0,
    branches_hit: int = 0,
    total: int | None = None,
    hit: int | None = None,
) -> FileCoverage:
    """Build a :class:`FileCoverage` from line sets, the one place percentages are derived.

    *total* / *hit* override the counts taken from the sets, for formats that
    state their own summary (lcov ``LF``/``LH``). Branch coverage is ``None``
    when the report named no branches, never 0%.
    """
    covered_set = set(covered)
    coverable_set = set(coverable)
    n_total = total if total is not None else len(coverable_set)
    n_hit = hit if hit is not None else len(covered_set)
    line_pct = n_hit / n_total * 100.0 if n_total else 0.0
    branch_pct = branches_hit / branches_found * 100.0 if branches_found else None
    return FileCoverage(
        file_path=file_path,
        line_coverage_pct=round(line_pct, 2),
        branch_coverage_pct=round(branch_pct, 2) if branch_pct is not None else None,
        covered_lines=sorted(covered_set),
        total_coverable_lines=n_total,
        coverable_lines=sorted(coverable_set),
    )


def parse_xml(text: str) -> ET.Element | None:
    """Root element of an XML report, or ``None`` when it does not parse.

    Parsers never raise on bad input; ``None`` becomes an empty report.
    External DTDs (JaCoCo declares one) are never fetched by ``xml.etree``.
    """
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        return None


def coverage_map_entry(fc: FileCoverage, source_format: str | None) -> dict:
    """The per-file dict ``HealthAnalyzer`` reads from its ``coverage_map``."""
    return {
        "line_coverage_pct": fc.line_coverage_pct,
        "branch_coverage_pct": fc.branch_coverage_pct,
        "covered_lines": list(fc.covered_lines),
        "total_coverable_lines": fc.total_coverable_lines,
        "source_format": source_format,
    }


@dataclass
class CoverageReport:
    """Whole-report bundle returned by every parser.

    ``source_format`` is a key of ``detector.PARSERS`` (``lcov``,
    ``cobertura``, ``clover``, ``repowise-json``, ...), or ``unknown``.
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

    # Tell pytest this ``Test``-prefixed class is not a test to collect.
    __test__ = False

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
