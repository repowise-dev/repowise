"""What runtime evidence is stored, how much of each run repo code explains, and how fresh it is."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC
from typing import Any

from repowise.core.analysis.health.coverage.freshness import coverage_freshness


def explained_share(total: float | None, explained: float | None) -> float | None:
    """The share of a run's value with a repo frame on the stack; ``None`` when unknown."""
    if total is None or explained is None or total <= 0:
        return None
    return round(min(explained / total, 1.0), 4)


def run_status(run: Any, reference_commit: str | None) -> dict[str, Any]:
    """One stored run as the status view shows it.

    *reference_commit* is the indexed commit: a run measured there is
    ``current``, at any other commit ``stale``, and ``unknown`` without one.
    """
    return {
        "run_id": run.id,
        "kind": run.kind,
        "workload": run.workload_name,
        "class": run.workload_class,
        "environment": run.environment,
        "value_kind": run.value_kind,
        "unit": run.unit,
        "tool": run.tool_name,
        "tool_version": run.tool_version,
        "commit_sha": run.commit_sha,
        "freshness": coverage_freshness(run.commit_sha, reference_commit),
        "dirty": run.dirty,
        # SQLite hands back the stored UTC time without its zone.
        "ingested_at": (
            run.ingested_at.replace(tzinfo=run.ingested_at.tzinfo or UTC).isoformat()
            if run.ingested_at
            else None
        ),
        "units": {
            "total": run.units_total,
            "matched": run.units_matched,
            "skipped": run.units_skipped,
            "outside_repo": run.units_outside_repo,
            "truncated": run.units_truncated,
        },
        "skipped": json.loads(run.skipped_json) if run.skipped_json else None,
        "total_value": run.total_value,
        "explained_share": explained_share(run.total_value, run.explained_value),
        "mapping_partial": bool(run.mapping_partial),
    }


def evidence_status(runs: Iterable[Any], reference_commit: str | None) -> dict[str, Any]:
    """The newest run of every workload (kind, name, environment), newest first,
    and how many runs each kind keeps. *runs* come newest first."""
    latest: dict[tuple[str, str, str], Any] = {}
    stored: dict[str, int] = {}
    for run in runs:
        stored[run.kind] = stored.get(run.kind, 0) + 1
        latest.setdefault((run.kind, run.workload_name, run.environment), run)
    return {
        "reference_commit": reference_commit,
        "runs_stored": dict(sorted(stored.items())),
        "workloads": [run_status(run, reference_commit) for run in latest.values()],
    }


__all__ = ["evidence_status", "explained_share", "run_status"]
