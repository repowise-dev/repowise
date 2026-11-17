"""CRUD operations for code-health findings, metrics, and snapshots
(repowise persistence layer).

Split by responsibility into ``findings``, ``metrics``, ``snapshots``,
``summary`` and ``exclude``. Every name the former single module defined is
re-exported here, private helpers included (``noqa: F401``, kept out of
``__all__``), so ``from ...crud.analysis.health import X`` is unaffected.
"""

from __future__ import annotations

from .exclude import (
    _filter_excluded_paths,  # noqa: F401
    health_exclude_spec,
)
from .findings import (
    _NOT_REFRESHED,  # noqa: F401
    _health_finding_row_kwargs,  # noqa: F401
    _insert_keeping_triage,  # noqa: F401
    gated_health_counts,
    get_deduction_by_path,
    get_health_findings,
    health_finding_priorities,
    open_finding_count,
    replace_governance_findings,
    save_health_findings,
    update_health_finding_status,
    upsert_health_findings,
)
from .metrics import (
    _health_metric_row_data,  # noqa: F401
    _opt,  # noqa: F401
    backfill_is_test,
    backfill_module_attribution,
    clear_unanalysed_scores,
    get_file_language_map,
    get_health_metrics,
    get_perf_coverage,
    get_scored_file_paths,
    prune_unscored_health_rows,
    save_health_metrics,
    sort_metrics_worst_first,
    upsert_health_metrics,
)
from .snapshots import (
    FILE_TREND_SNAPSHOT_WINDOW,
    HEALTH_SNAPSHOT_RETENTION,
    HealthSnapshotHeadline,
    HealthSnapshotScalars,
    _doc_drift_count,  # noqa: F401
    get_health_snapshot_file_counts,
    get_health_snapshot_headline,
    get_latest_snapshot_analyzer_version,
    list_health_snapshots,
    save_health_snapshot,
)
from .summary import (
    _open_finding_totals_rows,  # noqa: F401
    _rounded,  # noqa: F401
    get_headline_health,
    get_health_summary,
)

__all__ = [
    "FILE_TREND_SNAPSHOT_WINDOW",
    "HEALTH_SNAPSHOT_RETENTION",
    "HealthSnapshotHeadline",
    "HealthSnapshotScalars",
    "backfill_is_test",
    "backfill_module_attribution",
    "clear_unanalysed_scores",
    "gated_health_counts",
    "get_deduction_by_path",
    "get_file_language_map",
    "get_headline_health",
    "get_health_findings",
    "get_health_metrics",
    "get_health_snapshot_file_counts",
    "get_health_snapshot_headline",
    "get_health_summary",
    "get_latest_snapshot_analyzer_version",
    "get_perf_coverage",
    "get_scored_file_paths",
    "health_exclude_spec",
    "health_finding_priorities",
    "list_health_snapshots",
    "open_finding_count",
    "prune_unscored_health_rows",
    "replace_governance_findings",
    "save_health_findings",
    "save_health_metrics",
    "save_health_snapshot",
    "sort_metrics_worst_first",
    "update_health_finding_status",
    "upsert_health_findings",
    "upsert_health_metrics",
]
