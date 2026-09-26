"""Headline code-health KPIs for the overview."""

from __future__ import annotations

from typing import Any

from repowise.core.analysis.health.grading import (
    band_for,
)
from repowise.core.analysis.health.grading import (
    distribution as health_distribution,
)
from repowise.core.analysis.health.scoring import hotspot_health
from repowise.core.persistence.crud import (
    get_health_metrics as _get_health_metrics,
)
from repowise.core.persistence.crud import (
    get_health_summary as _get_health_summary,
)
from repowise.core.persistence.crud import get_hotspot_file_paths


async def _build_code_health(session: Any, repository: Any) -> dict[str, Any]:
    """Headline code-health KPIs; empty when health hasn't been run on this repo."""
    try:
        # Metrics first, handed to the summary: it reads the same table, and
        # that read now also aggregates the per-file deduction used to rank
        # floored files, so letting it load its own copy pays for the ranking
        # twice.
        metrics_rows = await _get_health_metrics(session, repository.id)
        if not metrics_rows:
            return {}
        health_summary = await _get_health_summary(session, repository.id, metrics=metrics_rows)
        # Hotspot health from the one owner, over the rows already loaded above.
        # This used to average the top 25% of files *by NLOC* under a comment
        # claiming it matched the dashboard; it never did. That ranks size, not
        # churn, and it disagreed with the persisted KPI on all 42 local indexes
        # (median 2.67 of 10, worst 6.46), reading higher on 31 of them. It also
        # sorted every file row to produce one float.
        hotspot_paths = await get_hotspot_file_paths(session, repository.id)
        return {
            "average_health": health_summary["average_health"],
            "band": band_for(float(health_summary["average_health"])),
            "hotspot_health": hotspot_health(metrics_rows, hotspot_paths),
            "worst_performer_path": health_summary["worst_performer_path"],
            "worst_performer_score": health_summary["worst_performer_score"],
            "open_findings": health_summary["open_findings"],
            "file_count": health_summary["file_count"],
            "distribution": health_distribution(metrics_rows),
        }
    except Exception:
        return {}
