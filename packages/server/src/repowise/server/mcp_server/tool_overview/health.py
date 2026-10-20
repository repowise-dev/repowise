"""Headline code-health KPIs for the overview."""

from __future__ import annotations

from typing import Any

from repowise.core.analysis.health.complexity.languages import NO_DIALECT_STATUS
from repowise.core.analysis.health.grading import (
    distribution as health_distribution,
)
from repowise.core.analysis.health.grading import headline_band
from repowise.core.analysis.health.rows import scored_rows, split_tests
from repowise.core.analysis.health.scoring import hotspot_health, nloc_weighted_score
from repowise.core.persistence.crud import (
    get_health_metrics as _get_health_metrics,
)
from repowise.core.persistence.crud import (
    get_health_summary as _get_health_summary,
)
from repowise.core.persistence.crud import get_hotspot_file_paths


def _production_average(metrics_rows: list[Any]) -> float | None:
    """The headline over non-test files, as ``compute_kpis`` weighs it; ``None``
    when the reading holds no tests, since it would only repeat the headline."""
    production, tests = split_tests(scored_rows(metrics_rows))
    if not production or not tests:
        return None
    return round(nloc_weighted_score(production), 2)


async def _build_code_health(session: Any, repository: Any) -> dict[str, Any]:
    """Headline code-health KPIs; empty when health hasn't been run on this repo.

    ``code_files`` is every code file health read, scored or not.
    """
    try:
        # Metrics first, handed to the summary so the shared table is read once.
        metrics_rows = await _get_health_metrics(session, repository.id)
        if not metrics_rows:
            return {}
        health_summary = await _get_health_summary(session, repository.id, metrics=metrics_rows)
        scored = health_summary["file_count"]
        code_files = scored + health_summary["unanalysed_file_count"]
        if health_summary["average_health"] is None:
            # Every file is in a language health has no dialect for.
            return {"analysis_status": NO_DIALECT_STATUS, "code_files": code_files}
        # Hotspot health from its one owner, over the rows already loaded.
        hotspot_paths = await get_hotspot_file_paths(session, repository.id)
        production = _production_average(metrics_rows)
        bands = health_distribution(metrics_rows)["bands"]
        return {
            "average_health": health_summary["average_health"],
            **({"production_average": production} if production is not None else {}),
            # ``None`` when too few code files were analysed to grade the repo.
            "band": headline_band(
                health_summary["average_health"], health_summary["analysed_share"]
            ),
            "hotspot_health": hotspot_health(metrics_rows, hotspot_paths),
            "worst_performer_path": health_summary["worst_performer_path"],
            "worst_performer_score": health_summary["worst_performer_score"],
            "open_findings": health_summary["open_findings"],
            "code_files": code_files,
            # Only said when some code files carry no score.
            **({"files_scored": scored} if scored != code_files else {}),
            # File counts only; get_health carries the NLOC-weighted split.
            "files_by_band": {band: row["files"] for band, row in bands.items()},
        }
    except Exception:
        return {}
