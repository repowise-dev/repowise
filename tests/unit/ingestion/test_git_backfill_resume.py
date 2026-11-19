"""Crash-resume contract for the git-tier backfill worker.

``backfill_full_tier`` brackets its run in a JobStore record so an interrupted
backfill is detectable and re-runnable. These tests cover the two failure
shapes:

* an in-process exception → the job is marked FAILED with the error;
* a hard kill mid-run → the job is left RUNNING (orphaned) and is reported by
  ``find_resumable`` so a re-run can pick it up (and completes cleanly);
  the re-run closes the orphan as FAILED so it does not stay RUNNING forever.

Closing an orphan is only safe while no other run can own it, so the re-run
reaps only while it holds the repo's update lock.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core.ingestion.git_indexer import GitIndexTier
from repowise.core.ingestion.git_indexer.backfill import BACKFILL_PHASE, backfill_full_tier
from repowise.core.persistence._interfaces.job_store import (
    ABANDONED_JOB_ERROR,
    JobRecord,
    JobState,
)
from repowise.core.procutils import process_create_token
from repowise.core.update_lock import release_update_lock, update_lock_path


class _FakeJobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, JobRecord] = {}
        self._seq = 0

    async def create_job(self, *, repository_id, phase, metadata=None) -> JobRecord:
        self._seq += 1
        jid = f"job{self._seq}"
        now = datetime.now(UTC)
        rec = JobRecord(
            jid, repository_id, phase, JobState.PENDING, None, now, now, None, metadata or {}
        )
        self.jobs[jid] = rec
        return rec

    async def update_state(self, job_id, state, *, cursor=None, error=None) -> JobRecord:
        old = self.jobs[job_id]
        rec = JobRecord(
            old.id, old.repository_id, old.phase, state, cursor or old.cursor,
            old.started_at, datetime.now(UTC), error, old.metadata,
        )
        self.jobs[job_id] = rec
        return rec

    async def find_resumable(self, *, repository_id=None) -> list[JobRecord]:
        return [
            j
            for j in self.jobs.values()
            if j.state in (JobState.PENDING, JobState.RUNNING)
            and (repository_id is None or j.repository_id == repository_id)
        ]


class _FlakyIndexer:
    """Fake GitIndexer that fails its first *fail_times* index_repo calls."""

    def __init__(self, repo_path: Path, fail_times: int = 0) -> None:
        self.repo_path = repo_path
        self.tier = GitIndexTier.ESSENTIAL
        self._fail_times = fail_times
        self.calls = 0

    async def index_repo(self, repo_id: str):
        self.calls += 1
        # backfill must force FULL for the duration of the run.
        assert self.tier is GitIndexTier.FULL
        if self.calls <= self._fail_times:
            raise RuntimeError("simulated git crash")
        return SimpleNamespace(files_indexed=3), [{"file_path": "a.py"}]


async def test_backfill_exception_marks_job_failed_and_restores_tier(tmp_path: Path):
    store = _FakeJobStore()
    indexer = _FlakyIndexer(tmp_path, fail_times=1)

    with pytest.raises(RuntimeError):
        await backfill_full_tier(indexer, "repo1", job_store=store)

    failed = [j for j in store.jobs.values() if j.state is JobState.FAILED]
    assert len(failed) == 1
    assert failed[0].phase == BACKFILL_PHASE
    assert failed[0].error and "simulated git crash" in failed[0].error
    # Tier is restored to its original value even on failure.
    assert indexer.tier is GitIndexTier.ESSENTIAL


async def test_orphaned_running_backfill_is_resumable_then_completes(tmp_path: Path):
    store = _FakeJobStore()

    # Simulate a hard kill mid-backfill: a RUNNING git.backfill job left behind.
    orphan = await store.create_job(repository_id="repo1", phase=BACKFILL_PHASE)
    await store.update_state(orphan.id, JobState.RUNNING)

    resumable = await store.find_resumable(repository_id="repo1")
    assert any(j.phase == BACKFILL_PHASE for j in resumable)

    # Re-run completes cleanly (FULL tier is re-run, per the worker's contract).
    indexer = _FlakyIndexer(tmp_path, fail_times=0)
    summary, results = await backfill_full_tier(indexer, "repo1", job_store=store)

    assert summary.files_indexed == 3
    assert results == [{"file_path": "a.py"}]
    completed = [j for j in store.jobs.values() if j.state is JobState.COMPLETED]
    assert len(completed) == 1
    assert completed[0].cursor == "3"


async def test_rerun_marks_orphaned_backfill_failed(tmp_path: Path):
    store = _FakeJobStore()
    orphan = await store.create_job(repository_id="repo1", phase=BACKFILL_PHASE)
    await store.update_state(orphan.id, JobState.RUNNING)

    await backfill_full_tier(_FlakyIndexer(tmp_path, fail_times=0), "repo1", job_store=store)

    assert store.jobs[orphan.id].state is JobState.FAILED
    assert store.jobs[orphan.id].error == ABANDONED_JOB_ERROR
    assert await store.find_resumable(repository_id="repo1") == []


async def test_rerun_leaves_other_repos_and_phases_alone(tmp_path: Path):
    store = _FakeJobStore()
    other_repo = await store.create_job(repository_id="repo2", phase=BACKFILL_PHASE)
    await store.update_state(other_repo.id, JobState.RUNNING)
    other_phase = await store.create_job(repository_id="repo1", phase="analysis")
    await store.update_state(other_phase.id, JobState.RUNNING)

    await backfill_full_tier(_FlakyIndexer(tmp_path, fail_times=0), "repo1", job_store=store)

    assert store.jobs[other_repo.id].state is JobState.RUNNING
    assert store.jobs[other_phase.id].state is JobState.RUNNING


async def test_backfill_runs_without_job_store(tmp_path: Path):
    """JobStore is optional — the worker still runs and forces FULL."""
    indexer = _FlakyIndexer(tmp_path, fail_times=0)
    summary, _results = await backfill_full_tier(indexer, "repo1")
    assert summary.files_indexed == 3
    assert indexer.tier is GitIndexTier.ESSENTIAL  # restored


def _hold_update_lock(repo_path: Path, pid: int) -> None:
    """An update lock owned by the live process *pid*."""
    payload = {
        "pid": pid,
        "pid_create_token": process_create_token(pid),
        "target_commit": None,
        "started_at": time.time(),
    }
    update_lock_path(repo_path).parent.mkdir(parents=True, exist_ok=True)
    update_lock_path(repo_path).write_text(json.dumps(payload))


async def _orphan(store: _FakeJobStore) -> JobRecord:
    orphan = await store.create_job(repository_id="repo1", phase=BACKFILL_PHASE)
    return await store.update_state(orphan.id, JobState.RUNNING)


async def test_a_lock_held_by_another_run_prevents_reaping(tmp_path: Path):
    # An upgrade backfills before it takes the update lock, so a concurrent
    # update (or upgrade) may own the open row: it must be left running.
    store = _FakeJobStore()
    live = await _orphan(store)
    _hold_update_lock(tmp_path, os.getppid())
    before = update_lock_path(tmp_path).read_text()

    summary, _ = await backfill_full_tier(_FlakyIndexer(tmp_path), "repo1", job_store=store)

    assert summary.files_indexed == 3
    assert store.jobs[live.id].state is JobState.RUNNING
    assert store.jobs[live.id].error is None
    # The other run's lock is untouched.
    assert update_lock_path(tmp_path).read_text() == before


async def test_a_free_lock_is_taken_for_the_reap_and_released(tmp_path: Path):
    store = _FakeJobStore()
    orphan = await _orphan(store)

    await backfill_full_tier(_FlakyIndexer(tmp_path), "repo1", job_store=store)

    assert store.jobs[orphan.id].state is JobState.FAILED
    assert not update_lock_path(tmp_path).exists()


async def test_a_lock_this_process_holds_is_kept(tmp_path: Path):
    store = _FakeJobStore()
    orphan = await _orphan(store)
    _hold_update_lock(tmp_path, os.getpid())
    try:
        await backfill_full_tier(_FlakyIndexer(tmp_path), "repo1", job_store=store)
        assert store.jobs[orphan.id].state is JobState.FAILED
        assert update_lock_path(tmp_path).exists()
    finally:
        release_update_lock(tmp_path)
