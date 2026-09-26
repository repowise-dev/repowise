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
        # Metrics first, handed to the summary so the shared table is read once.
        metrics_rows = await _get_health_metrics(session, repository.id)
        if not metrics_rows:
            return {}
        health_summary = await _get_health_summary(session, repository.id, metrics=metrics_rows)
        # Hotspot health from its one owner, over the rows already loaded.
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
