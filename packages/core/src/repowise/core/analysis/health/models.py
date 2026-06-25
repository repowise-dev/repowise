"""Dataclasses returned by the health analyzer.

These are language-agnostic plain dataclasses, mirrored by SQLAlchemy ORM
counterparts in ``persistence/models.py`` (``HealthFinding``,
``HealthFileMetric``, ``HealthSnapshot``).
"""

from __future__ import annotations

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
