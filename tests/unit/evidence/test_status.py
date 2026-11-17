"""The evidence status view: freshness against the index, explained share."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from repowise.core.evidence.status import evidence_status, explained_share


def _run(run_id: str, workload: str, commit: str | None, **fields) -> SimpleNamespace:
    base = {
        "id": run_id, "kind": "profile", "workload_name": workload,
        "workload_class": "entry_point", "environment": "local", "value_kind": "wall",
        "unit": "samples", "tool_name": None, "tool_version": None, "commit_sha": commit,
        "dirty": None, "ingested_at": datetime(2026, 1, 1, tzinfo=UTC), "units_total": None,
        "units_matched": None, "units_skipped": None, "units_outside_repo": None,
        "units_truncated": None, "skipped_json": None, "total_value": None,
        "explained_value": None, "mapping_partial": False,
    }
    return SimpleNamespace(**{**base, **fields})


def test_freshness_is_against_the_indexed_commit() -> None:
    head = "a" * 40
    status = evidence_status(
        [_run("r3", "build", head), _run("r2", "test", "b" * 40), _run("r1", "build", "c" * 40),
         _run("r0", "load", None)],
        head,
    )
    assert status["runs_stored"] == {"profile": 4}
    assert [(w["run_id"], w["freshness"]) for w in status["workloads"]] == [
        ("r3", "current"), ("r2", "stale"), ("r0", "unknown"),
    ]
    # Unknown values stay unknown.
    assert status["workloads"][0]["units"]["matched"] is None
    assert status["workloads"][0]["skipped"] is None


def test_explained_share_needs_both_values() -> None:
    assert explained_share(5300, 583) == 0.11
    assert explained_share(None, 5) is None
    assert explained_share(0, 0) is None
    assert explained_share(10, 12) == 1.0
