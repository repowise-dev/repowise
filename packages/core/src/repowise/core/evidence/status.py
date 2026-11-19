"""What runtime evidence is stored, where each run's time went, and how fresh it is."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC
from typing import Any

from repowise.core.analysis.health.coverage.freshness import coverage_freshness


def run_share(total: float | None, value: float | None) -> float | None:
    """*value*'s share of a run's *total*, rounded; ``None`` when either is unknown."""
    if total is None or value is None or total <= 0:
        return None
    return round(min(value / total, 1.0), 4)


def time_split(run: Any) -> dict[str, float | None]:
    """Where a stored profile run's time went: production repo code itself (waits
    and libraries excluded), waits, and libraries. ``None`` for a run stored
    before the split was recorded, or one whose file did not carry it."""
    total = run.total_value
    return {
        "repo_self_share": run_share(total, run.repo_self_value),
        "wait_share": run_share(total, run.wait_value),
        "library_share": run_share(total, run.library_value),
    }


def split_words(shares: Mapping[str, float | None]) -> str | None:
    """The split in words, repo code first: ``repo code itself 47%, waits 30%,
    libraries 12%``. ``None`` when the run did not record it."""
    repo = shares.get("repo_self_share")
    if repo is None:
        return None
    parts = [f"repo code itself {repo:.0%}"]
    for key, word in (("wait_share", "waits"), ("library_share", "libraries")):
        if shares.get(key) is not None:
            parts.append(f"{word} {shares[key]:.0%}")
    return ", ".join(parts)


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
        **time_split(run),
        "mapping_partial": bool(run.mapping_partial),
        "tests_failed": run.tests_failed,
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


__all__ = ["evidence_status", "run_share", "run_status", "split_words", "time_split"]
