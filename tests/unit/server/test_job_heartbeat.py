"""Tests for JobProgressCallback's independent wall-clock heartbeat.

The stale-job sweep (``app.reset_workspace_stale_jobs`` /
``app.lifespan``) and the active-job concurrency guard
(``crud.has_active_job``) both read a running job's ``updated_at`` as a
liveness heartbeat. That heartbeat cannot be derived only from
``on_item_done``/``on_phase_start`` progress events: some pipeline phases
(e.g. ``knowledge_graph.skeleton`` / ``knowledge_graph.enrich`` in
``core/pipeline/orchestrator.py``) report a single ``on_phase_start`` and
then do one long unit of work with no progress events at all until the next
phase starts. Without an independent timer, a job stuck in such a phase for
longer than ``JOB_HEARTBEAT_STALE_AFTER`` would look abandoned even though
it is genuinely still running -- reopening exactly the double-run race this
heartbeat mechanism exists to close.
"""

from __future__ import annotations

import asyncio

import pytest

from repowise.core.persistence.crud import (
    get_generation_job,
    has_active_job,
    update_job_status,
    upsert_generation_job,
    upsert_repository,
)
from repowise.server.job_executor import JobProgressCallback


async def _seed_running_job(session_factory) -> tuple[str, str]:
    async with session_factory() as session:
        repo = await upsert_repository(session, name="acme/widgets", local_path="/repos/acme")
        job = await upsert_generation_job(session, repository_id=repo.id, status="pending")
        await session.commit()
        repo_id, job_id = repo.id, job.id
    async with session_factory() as session:
        await update_job_status(session, job_id, "running")
        await session.commit()
    return repo_id, job_id


@pytest.mark.asyncio
async def test_heartbeat_survives_a_phase_with_no_progress_events(session_factory):
    """A silent phase (on_phase_start, then nothing) must not go stale."""
    repo_id, job_id = await _seed_running_job(session_factory)

    progress = JobProgressCallback(job_id, session_factory, heartbeat_interval_s=0.05)
    progress.start_heartbeat()
    try:
        progress.on_phase_start("knowledge_graph.skeleton", None)
        async with session_factory() as session:
            before = (await get_generation_job(session, job_id)).updated_at

        # No on_item_done calls at all during this window -- the exact shape
        # of a single-item phase awaiting one long synchronous/LLM call.
        await asyncio.sleep(0.3)

        async with session_factory() as session:
            after = await get_generation_job(session, job_id)
            still_active = await has_active_job(session, repo_id)

        assert after.updated_at > before, "heartbeat did not advance during a silent phase"
        assert still_active is True, "guard must not treat a silently-working job as abandoned"
    finally:
        await progress.drain_and_stop()


@pytest.mark.asyncio
async def test_drain_and_stop_actually_stops_the_heartbeat_loop(session_factory):
    """No further writes, and no leaked task, once the job callback is stopped."""
    _repo_id, job_id = await _seed_running_job(session_factory)

    progress = JobProgressCallback(job_id, session_factory, heartbeat_interval_s=0.05)
    progress.start_heartbeat()
    await asyncio.sleep(0.1)
    await progress.drain_and_stop()

    async with session_factory() as session:
        stamp_after_stop = (await get_generation_job(session, job_id)).updated_at
    await asyncio.sleep(0.2)
    async with session_factory() as session:
        stamp_later = (await get_generation_job(session, job_id)).updated_at

    assert stamp_after_stop == stamp_later, "heartbeat kept writing after drain_and_stop()"
    assert progress._heartbeat_task is None


@pytest.mark.asyncio
async def test_start_heartbeat_is_a_noop_after_stop(session_factory):
    """Calling start_heartbeat() after drain_and_stop() must not resurrect the loop."""
    _repo_id, job_id = await _seed_running_job(session_factory)

    progress = JobProgressCallback(job_id, session_factory, heartbeat_interval_s=0.05)
    await progress.drain_and_stop()
    progress.start_heartbeat()
    assert progress._heartbeat_task is None
