"""Integration tests for --mode fast and pipeline checkpointing.

Runs the real pipeline against the sample repo (no LLM, no external services)
to prove:
  * FAST mode skips doc generation and the co-change walk (ESSENTIAL git tier);
  * a supplied JobStore receives a COMPLETED record for each major phase;
  * an interrupted phase (left RUNNING) is excluded from the resume set.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from repowise.core.persistence._interfaces.job_store import JobRecord, JobState
from repowise.core.pipeline import run_pipeline
from repowise.core.pipeline.checkpoint import find_completed_phases
from repowise.core.pipeline.modes import OrchestratorMode


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

    async def list_jobs(self, *, repository_id=None, phase=None, state=None, limit=100):
        return list(self.jobs.values())


async def test_fast_mode_skips_generation_and_co_change(sample_repo_path: Path) -> None:
    store = _FakeJobStore()
    result = await run_pipeline(
        sample_repo_path,
        generate_docs=True,  # FAST must override this to False (no LLM client passed)
        mode=OrchestratorMode.FAST,
        job_store=store,
    )

    # No LLM generation happened.
    assert result.generated_pages is None
    # ESSENTIAL git tier: per-file metadata exists but no co-change partners.
    assert result.git_metadata_list, "expected git metadata for sample repo"
    for meta in result.git_metadata_list:
        assert meta.get("co_change_partners_json", "[]") == "[]"


async def test_checkpoints_recorded_for_completed_phases(sample_repo_path: Path) -> None:
    store = _FakeJobStore()
    await run_pipeline(
        sample_repo_path,
        mode=OrchestratorMode.FAST,
        job_store=store,
    )

    completed = await find_completed_phases(store, str(Path(sample_repo_path).resolve()))
    # parse + git always run; both should be recorded COMPLETED.
    assert "parse" in completed
    assert "git" in completed
    # Every recorded job reached a terminal COMPLETED state on the success path.
    assert all(j.state == JobState.COMPLETED for j in store.jobs.values())


async def test_interrupted_phase_excluded_from_resume_set() -> None:
    store = _FakeJobStore()
    j = await store.create_job(repository_id="r", phase="generation")
    await store.update_state(j.id, JobState.RUNNING)  # simulate a crash mid-phase
    completed = await find_completed_phases(store, "r")
    assert "generation" not in completed
