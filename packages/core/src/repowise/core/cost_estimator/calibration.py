"""Telemetry-based calibration from prior generation runs.

When ``.repowise/db.sqlite`` exists and has ``wiki_pages`` rows for the
target repo, we read actual ``input_tokens`` / ``output_tokens`` per
page_type and use the per-type average instead of the static
heuristics. This makes the **second** run of ``repowise init`` /
``repowise update`` on the same repo highly accurate.

The function is intentionally tolerant of missing tables and schema
drift — any error short-circuits to ``{}`` so the heuristic path runs.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


def load_telemetry_averages(repo_path: Path | str) -> dict[str, tuple[float, float]]:
    """Return ``{page_type: (avg_input, avg_output)}`` from prior runs.

    Returns an empty dict when no telemetry is available — the caller
    falls back to static heuristics.
    """
    db_path = Path(repo_path) / ".repowise" / "db.sqlite"
    if not db_path.exists():
        return {}

    try:
        with sqlite3.connect(str(db_path)) as conn:
            cur = conn.execute(
                """
                SELECT page_type, AVG(input_tokens), AVG(output_tokens), COUNT(*)
                FROM wiki_pages
                WHERE input_tokens > 0 AND output_tokens > 0
                GROUP BY page_type
                """
            )
            rows = cur.fetchall()
    except sqlite3.Error as exc:
        log.debug("cost_estimator.telemetry_load_failed", error=str(exc))
        return {}

    averages: dict[str, tuple[float, float]] = {}
    for page_type, avg_in, avg_out, n in rows:
        # Require at least 3 historical samples per page_type to trust
        # the average — fewer than that is dominated by outliers.
        if n is None or n < 3:
            continue
        averages[str(page_type)] = (float(avg_in or 0.0), float(avg_out or 0.0))
    return averages
