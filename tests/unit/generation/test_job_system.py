"""Tests for generation/job_system.py — 22 tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.generation.job_system import Checkpoint, JobSystem
from repowise.core.generation.models import GenerationConfig


def _make_system(tmp_path: Path) -> JobSystem:
    return JobSystem(tmp_path / "jobs")


def _create(js: JobSystem) -> str:
    return js.create_job(".", GenerationConfig(), "mock", "mock-model-1")


# ---------------------------------------------------------------------------
# create_job
# ---------------------------------------------------------------------------


def test_create_job_returns_uuid(tmp_path):
    js = _make_system(tmp_path)
    job_id = _create(js)
    assert isinstance(job_id, str)
    assert len(job_id) == 36  # UUID format


def test_create_job_writes_json_file(tmp_path):
    js = _make_system(tmp_path)
    job_id = _create(js)
    json_file = tmp_path / "jobs" / f"{job_id}.json"
    assert json_file.exists()


def test_create_job_status_is_pending(tmp_path):
    js = _make_system(tmp_path)
    job_id = _create(js)
    cp = js.get_checkpoint(job_id)
    assert cp.status == "pending"


# ---------------------------------------------------------------------------
# start_job
# ---------------------------------------------------------------------------


def test_start_job_sets_running(tmp_path):
    js = _make_system(tmp_path)
    job_id = _create(js)
    js.start_job(job_id, 10)
    cp = js.get_checkpoint(job_id)
    assert cp.status == "running"


def test_start_job_stores_total_pages(tmp_path):
    js = _make_system(tmp_path)
    job_id = _create(js)
    js.start_job(job_id, 42)
    cp = js.get_checkpoint(job_id)
    assert cp.total_pages == 42


# ---------------------------------------------------------------------------
# complete_page
# ---------------------------------------------------------------------------


def test_complete_page_increments_count(tmp_path):
    js = _make_system(tmp_path)
    job_id = _create(js)
    js.start_job(job_id, 5)
    js.complete_page(job_id, "file_page:main.py")
    cp = js.get_checkpoint(job_id)
    assert cp.completed_pages == 1
    assert "file_page:main.py" in cp.completed_page_ids


def test_complete_page_idempotent(tmp_path):
    js = _make_system(tmp_path)
    job_id = _create(js)
    js.start_job(job_id, 5)
    js.complete_page(job_id, "file_page:main.py")
    js.complete_page(job_id, "file_page:main.py")
    cp = js.get_checkpoint(job_id)
    assert cp.completed_pages == 1  # not incremented twice


def test_complete_page_json_persisted(tmp_path):
    js = _make_system(tmp_path)
    job_id = _create(js)
    js.start_job(job_id, 5)
    js.complete_page(job_id, "file_page:x.py")
    # New instance — reads from disk
    js2 = JobSystem(tmp_path / "jobs")
    cp = js2.get_checkpoint(job_id)
    assert "file_page:x.py" in cp.completed_page_ids


# ---------------------------------------------------------------------------
# fail_page
# ---------------------------------------------------------------------------


def test_fail_page_increments_failed(tmp_path):
    js = _make_system(tmp_path)
    job_id = _create(js)
    js.start_job(job_id, 5)
    js.fail_page(job_id, "file_page:broken.py", "ParseError")
    cp = js.get_checkpoint(job_id)
    assert cp.failed_pages == 1


def test_fail_page_job_stays_running(tmp_path):
    js = _make_system(tmp_path)
    job_id = _create(js)
    js.start_job(job_id, 5)
    js.fail_page(job_id, "file_page:broken.py", "ParseError")
    cp = js.get_checkpoint(job_id)
    assert cp.status == "running"


# ---------------------------------------------------------------------------
# complete_job / fail_job
# ---------------------------------------------------------------------------


def test_complete_job_sets_completed(tmp_path):
    js = _make_system(tmp_path)
    job_id = _create(js)
    js.start_job(job_id, 1)
    js.complete_job(job_id)
    cp = js.get_checkpoint(job_id)
    assert cp.status == "completed"


def test_fail_job_stores_error_message(tmp_path):
    js = _make_system(tmp_path)
    job_id = _create(js)
    js.start_job(job_id, 1)
    js.fail_job(job_id, "Provider unavailable")
    cp = js.get_checkpoint(job_id)
    assert cp.status == "failed"
    assert cp.error_message == "Provider unavailable"


# ---------------------------------------------------------------------------
# pause / resume
# ---------------------------------------------------------------------------


def test_pause_job_sets_paused(tmp_path):
    js = _make_system(tmp_path)
    job_id = _create(js)
    js.start_job(job_id, 5)
    js.pause_job(job_id)
    cp = js.get_checkpoint(job_id)
    assert cp.status == "paused"


def test_resume_job_sets_running(tmp_path):
    js = _make_system(tmp_path)
    job_id = _create(js)
    js.start_job(job_id, 5)
    js.pause_job(job_id)
    cp = js.resume_job(job_id)
    assert cp.status == "running"


def test_resume_job_returns_checkpoint(tmp_path):
    js = _make_system(tmp_path)
    job_id = _create(js)
    js.start_job(job_id, 5)
    js.pause_job(job_id)
    cp = js.resume_job(job_id)
    assert isinstance(cp, Checkpoint)


# ---------------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------------


def test_invalid_transition_raises_value_error(tmp_path):
    js = _make_system(tmp_path)
    job_id = _create(js)
    js.start_job(job_id, 1)
    js.complete_job(job_id)
    with pytest.raises(ValueError):
        js.complete_job(job_id)  # completed → running doesn't exist


# ---------------------------------------------------------------------------
# JSON round-trip persistence
# ---------------------------------------------------------------------------


def test_json_round_trip_persistence(tmp_path):
    js = _make_system(tmp_path)
    job_id = _create(js)
    js.start_job(job_id, 3)
    js.complete_page(job_id, "file_page:a.py")
    js.complete_page(job_id, "file_page:b.py")

    # Reload from disk with a new instance
    js2 = JobSystem(tmp_path / "jobs")
    cp = js2.get_checkpoint(job_id)
    assert cp.completed_pages == 2
    assert "file_page:a.py" in cp.completed_page_ids
    assert "file_page:b.py" in cp.completed_page_ids


# ---------------------------------------------------------------------------
# list_jobs
# ---------------------------------------------------------------------------


def test_list_jobs_sorted_by_created_at_desc(tmp_path):
    js = _make_system(tmp_path)
    j1 = _create(js)
    j2 = _create(js)
    jobs = js.list_jobs()
    ids = [j.job_id for j in jobs]
    # j2 was created after j1 so it should appear first
    assert ids.index(j2) <= ids.index(j1)


def test_list_jobs_returns_all(tmp_path):
    js = _make_system(tmp_path)
    _create(js)
    _create(js)
    _create(js)
    assert len(js.list_jobs()) == 3


# ---------------------------------------------------------------------------
# get_completed_page_ids
# ---------------------------------------------------------------------------


def test_get_completed_page_ids_correct_set(tmp_path):
    js = _make_system(tmp_path)
    job_id = _create(js)
    js.start_job(job_id, 5)
    js.complete_page(job_id, "file_page:x.py")
    js.complete_page(job_id, "module_page:pkg")
    ids = js.get_completed_page_ids(job_id)
    assert ids == {"file_page:x.py", "module_page:pkg"}
