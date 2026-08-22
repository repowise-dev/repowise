"""CRUD operations for the analysis domain (repowise persistence layer).

Split out of the former monolithic ``crud.py`` and later from the single
``analysis.py`` module into per-entity submodules. ``crud/__init__.py``
re-exports every public name via ``from .analysis import *``, so existing
imports are unaffected.
"""

from __future__ import annotations

from . import coverage, coverage_map, dead_code, health, refactoring  # noqa: F401
from .coverage import (
    get_coverage_summary,
    load_coverage_for_repo,
    save_coverage_files,
)
from .coverage_map import (
    covered_source_files,
    files_covered_by,
    get_test_coverage_summary,
    save_test_coverage,
    tests_covering,
    tests_covering_files,
)
from .dead_code import (
    get_dead_code_findings,
    get_dead_code_summary,
    replace_dead_code_findings,
    save_dead_code_findings,
    update_dead_code_status,
)
from .health import (
    FILE_TREND_SNAPSHOT_WINDOW,
    HEALTH_SNAPSHOT_RETENTION,
    HealthSnapshotHeadline,
    HealthSnapshotScalars,
    backfill_module_attribution,
    get_average_health,
    get_deduction_by_path,
    get_file_language_map,
    get_health_findings,
    get_health_metrics,
    get_health_snapshot_file_counts,
    get_health_snapshot_headline,
    get_health_summary,
    get_perf_coverage,
    list_health_snapshots,
    replace_governance_findings,
    save_health_findings,
    save_health_metrics,
    save_health_snapshot,
    sort_metrics_worst_first,
    update_health_finding_status,
    upsert_health_findings,
    upsert_health_metrics,
)
from .refactoring import (
    get_refactoring_suggestion,
    get_refactoring_suggestions,
    save_refactoring_suggestions,
    upsert_refactoring_suggestions,
)

__all__ = [
    "FILE_TREND_SNAPSHOT_WINDOW",
    "HEALTH_SNAPSHOT_RETENTION",
    "HealthSnapshotHeadline",
    "HealthSnapshotScalars",
    "backfill_module_attribution",
    "covered_source_files",
    "files_covered_by",
    "get_average_health",
    "get_coverage_summary",
    "get_dead_code_findings",
    "get_dead_code_summary",
    "get_deduction_by_path",
    "get_file_language_map",
    "get_health_findings",
    "get_health_metrics",
    "get_health_snapshot_file_counts",
    "get_health_snapshot_headline",
    "get_health_summary",
    "get_perf_coverage",
    "get_refactoring_suggestion",
    "get_refactoring_suggestions",
    "get_test_coverage_summary",
    "list_health_snapshots",
    "load_coverage_for_repo",
    "replace_dead_code_findings",
    "replace_governance_findings",
    "save_coverage_files",
    "save_dead_code_findings",
    "save_health_findings",
    "save_health_metrics",
    "save_health_snapshot",
    "save_refactoring_suggestions",
    "save_test_coverage",
    "sort_metrics_worst_first",
    "tests_covering",
    "tests_covering_files",
    "update_dead_code_status",
    "update_health_finding_status",
    "upsert_health_findings",
    "upsert_health_metrics",
    "upsert_refactoring_suggestions",
]
