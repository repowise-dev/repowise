"""Pure section builders of ``services/overview_payload``."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from repowise.server.services.overview_payload import (
    count_silos,
    defect_accuracy,
    snapshot_history,
    sync_times,
)

_T0 = datetime(2026, 9, 1, tzinfo=UTC)
_T1 = datetime(2026, 9, 2, tzinfo=UTC)
_T2 = datetime(2026, 9, 3, tzinfo=UTC)


def test_count_silos_needs_one_owner_over_80_percent_of_files() -> None:
    rows = [("a/x.py", "ann")] * 5 + [("b/x.py", "ann"), ("b/y.py", "bob"), ("c/z.py", None)]
    assert count_silos(rows) == 1


def _job(status: str, mode: str | None, finished_at=None, **kw):
    config = "{}" if mode is None else f'{{"mode": "{mode}"}}'
    return SimpleNamespace(
        id=kw.get("id", "j"),
        status=status,
        config_json=config,
        finished_at=finished_at,
        model_name=kw.get("model_name"),
    )


def test_sync_times_splits_resync_from_sync_and_finds_active_job() -> None:
    jobs = [
        _job("running", None, id="active"),
        _job("completed", "full_resync", _T2),
        _job("completed", "incremental", _T1, model_name="m1"),
    ]
    assert sync_times(jobs, None) == {
        "last_sync_at": _T1.isoformat(),
        "last_resync_at": _T2.isoformat(),
        "last_sync_model": "m1",
        "active_job_id": "active",
    }


def test_sync_times_falls_back_to_repo_updated_at_only_after_resync() -> None:
    resync = [_job("completed", "full_resync", _T1)]
    assert sync_times(resync, _T2)["last_sync_at"] == _T2.isoformat()
    assert sync_times(resync, _T0)["last_sync_at"] is None
    assert sync_times([], _T0)["last_sync_at"] == _T0.isoformat()


def test_sync_times_reports_the_later_of_the_sync_job_and_the_row_stamp() -> None:
    # A CLI sync after the last server job: the stamp is the last sync, and the
    # job's model does not describe it.
    newer_cli = sync_times([_job("completed", "incremental", _T0, model_name="m1")], _T2)
    assert newer_cli["last_sync_at"] == _T2.isoformat()
    assert newer_cli["last_sync_model"] is None

    # A server job after the stamp (a job stamps the row before it finishes).
    newer_job = sync_times([_job("completed", "incremental", _T2, model_name="m1")], _T1)
    assert newer_job["last_sync_at"] == _T2.isoformat()
    assert newer_job["last_sync_model"] == "m1"


def test_sync_times_compares_a_naive_row_stamp_as_utc() -> None:
    naive_stamp = datetime(2026, 9, 3)
    jobs = [_job("completed", "incremental", _T1)]
    assert sync_times(jobs, naive_stamp)["last_sync_at"] == naive_stamp.isoformat()


def test_defect_accuracy_survives_a_malformed_details_blob() -> None:
    metric = SimpleNamespace(file_path="a.py", score=3.0, nloc=10, has_test_file=False, module="a")
    bad = SimpleNamespace(
        file_path="a.py", biomarker_type="prior_defect", details_json="{not json", health_impact=1.0
    )
    # Must not raise; the panel is best-effort.
    defect_accuracy([metric], [bad])


def test_snapshot_history_rounds_and_keeps_missing_hotspot_none() -> None:
    snap = SimpleNamespace(
        recent=[SimpleNamespace(taken_at=None, average_health=7.456, hotspot_health=None)]
    )
    assert snapshot_history(snap) == [
        {"taken_at": None, "average_health": 7.46, "hotspot_health": None}
    ]
