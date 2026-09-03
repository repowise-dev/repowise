"""Dataclasses returned by the health analyzer.

These are language-agnostic plain dataclasses, mirrored by SQLAlchemy ORM
counterparts in ``persistence/models.py`` (``HealthFinding``,
``HealthFileMetric``, ``HealthSnapshot``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class HealthFindingData:
    """One biomarker hit. Persisted as a ``HealthFinding`` row."""

    biomarker_type: str
    severity: Severity
    file_path: str
    function_name: str | None
    line_start: int | None
    line_end: int | None
    details: dict[str, Any]
    health_impact: float
    reason: str = ""
    # The finding's "home" health dimension (``defect`` / ``maintainability`` /
    # ``performance``) for per-pillar filtering. Defaults to ``defect`` - the
    # historical, surfaced pillar - so callers that don't set it are unchanged.
    dimension: str = "defect"


def primary_finding(findings: Sequence[Any]) -> Any | None:
    """The one finding that best names why a file is unhealthy.

    The strongest **discrete** finding wins. A continuous biomarker fires on
    every file carrying its input signal, so on a repo with coverage data
    ``coverage_gradient`` takes the max-impact tiebreak nearly everywhere: it
    led 22 of the top 50 worst files with "N% of lines uncovered", which is true
    and says nothing about why this file rather than any other. It still leads
    when it is a file's only finding.

    Extracted because the rule was written out four times - the MCP file leads,
    the REST work queue, the code-health serializers and the CLI table - and
    only the MCP copy remembered the continuous exclusion. All four read this
    one now. Adopting it did change what three of them lead with, which is why
    it was a surface decision rather than a refactor: on the dogfood index 134
    of 3,011 files moved, and every one of them had been led by
    ``coverage_gradient``.
    """
    from .biomarkers.registry import continuous_biomarkers

    if not findings:
        return None
    continuous = continuous_biomarkers()
    discrete = [item for item in findings if item.biomarker_type not in continuous]
    return max(discrete or findings, key=lambda item: float(item.health_impact or 0.0))


def primary_biomarker_by_file(findings: Iterable[Any]) -> dict[str, str]:
    """Each file's dominant cause, keyed by path. See :func:`primary_finding`."""
    by_file: dict[str, list[Any]] = {}
    for finding in findings:
        by_file.setdefault(finding.file_path, []).append(finding)
    leads = {path: primary_finding(items) for path, items in by_file.items()}
    return {path: lead.biomarker_type for path, lead in leads.items() if lead is not None}


@dataclass
class HealthFileMetricData:
    """Per-file aggregate. Persisted as a ``HealthFileMetric`` row."""

    file_path: str
    score: float
    max_ccn: int
    max_nesting: int
    nloc: int
    has_test_file: bool
    module: str | None = None
    duplication_pct: float | None = None
    line_coverage_pct: float | None = None
    branch_coverage_pct: float | None = None
    # Per-dimension scores from the three-signal split. ``score`` above stays the
    # overall surfaced number and equals ``defect_score`` for now (the overall
    # score is not blended until a later, deliberate decision). ``performance_score``
    # is ``None`` until the performance detectors land. All nullable/defaulted so
    # the split is additive.
    defect_score: float | None = None
    maintainability_score: float | None = None
    performance_score: float | None = None


@dataclass
class HealthReport:
    repo_id: str
    analyzed_at: datetime
    findings: list[HealthFindingData] = field(default_factory=list)
    metrics: list[HealthFileMetricData] = field(default_factory=list)
    # Repo-level KPIs computed by ``scoring.compute_kpis``.
    kpis: dict[str, Any] = field(default_factory=dict)
    # Per-function blame rollup rows (``git_function_blame``), derived from the
    # FULL-tier blame index. Empty on ESSENTIAL tier / when blame is absent.
    function_blame_rows: list[dict] = field(default_factory=list)
    # Deterministic refactoring suggestions (``RefactoringSuggestion``), one
    # per detected opportunity. Produced by the refactoring layer in the same
    # per-file pass that produces findings; empty when the layer is disabled
    # or no opportunity is found. Typed ``Any`` to avoid importing the
    # refactoring package here (it imports this module).
    refactoring_suggestions: list[Any] = field(default_factory=list)
    # Resolved coverage rows ingested for this run (``FileCoverage`` with
    # canonical repo keys), plus the source format. Populated by the pipeline
    # when a coverage report is discovered/passed; the persister writes these
    # to the ``coverage_files`` table. Empty when no coverage was ingested.
    coverage_files: list[Any] = field(default_factory=list)
    coverage_format: str | None = None
    # True when the ingested coverage report mapped fewer than half its files
    # to the repo tree (#1746); carried so the persister can stamp the rows.
    coverage_mapping_partial: bool = False
    # Incremental writers replace all dimensions only for ``authoritative_paths``.
    # ``performance_authoritative_paths`` may be wider: the bounded execution
    # closure whose performance rows/plans were recomputed for full parity.
    authoritative_paths: set[str] = field(default_factory=set)
    performance_authoritative_paths: set[str] = field(default_factory=set)
    # Plan policy for the writer that persists these findings. The performance
    # plans in ``refactoring_suggestions`` above are a report-level convenience
    # built from one run's findings; the authoritative ones are generated once,
    # against the merged stored set, and need this configuration to get there.
    # Typed ``Any`` for the same reason as ``refactoring_suggestions``.
    performance_plan_policy: Any | None = None
