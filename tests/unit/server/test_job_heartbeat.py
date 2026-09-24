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

The timer is an ``asyncio.Task`` on the *same event loop* as the pipeline,
which is a real limit on what it can survive: ``asyncio.sleep`` only fires
when the loop gets a chance to run it, so a phase that calls a long
synchronous function *directly* (not via ``await asyncio.to_thread(...)``)
blocks the whole loop, and the heartbeat's own timer cannot wake up either,
no matter the interval. The two tests at the bottom of this file cover both
sides of that distinction on purpose: offloaded-via-to_thread survives,
un-offloaded does not -- because the fix for the second case can only ever
be "don't block the loop," never a cleverer heartbeat.
"""

from __future__ import annotations

import asyncio
import time

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
async def test_heartbeat_survives_a_silent_but_yielding_phase(session_factory):
    """A silent phase that still yields to the loop (e.g. an awaited task,
    like knowledge_graph.enrich) must not go stale, even with zero
    on_item_done calls. This covers the awaited case only -- see the two
    to_thread tests below for the blocking case, which this cannot exercise
    because ``asyncio.sleep`` itself yields.
    """
    repo_id, job_id = await _seed_running_job(session_factory)

    progress = JobProgressCallback(job_id, session_factory, heartbeat_interval_s=0.05)
    progress.start_heartbeat()
    try:
        progress.on_phase_start("knowledge_graph.enrich", None)
        async with session_factory() as session:
            before = (await get_generation_job(session, job_id)).updated_at

        # No on_item_done calls during this window, but it yields (as a real
        # awaited task would), so the heartbeat can still interleave.
        await asyncio.sleep(0.3)

        async with session_factory() as session:
            after = await get_generation_job(session, job_id)
            still_active = await has_active_job(session, repo_id)

        assert after.updated_at > before, "heartbeat did not advance during a silent phase"
        assert still_active is True, "guard must not treat a silently-working job as abandoned"
    finally:
        await progress.drain_and_stop()


@pytest.mark.asyncio
async def test_heartbeat_survives_a_blocking_call_offloaded_via_to_thread(session_factory):
    """The production fix for a blocking phase (knowledge_graph.skeleton,
    _read_sources, wire_tsconfig_resolver in core/pipeline) is to run it via
    ``asyncio.to_thread``, not to make the heartbeat itself smarter -- an
    asyncio.Task's timer cannot fire while something else runs synchronously
    on the same event loop thread. This proves the pattern: a genuinely
    blocking call (real ``time.sleep``, not ``asyncio.sleep``) offloaded via
    to_thread lets the heartbeat keep ticking throughout, because the event
    loop thread itself stays free while the worker thread blocks instead.
    """
    repo_id, job_id = await _seed_running_job(session_factory)

    progress = JobProgressCallback(job_id, session_factory, heartbeat_interval_s=0.05)
    progress.start_heartbeat()
    try:
        async with session_factory() as session:
            before = (await get_generation_job(session, job_id)).updated_at

        await asyncio.to_thread(time.sleep, 0.3)

        async with session_factory() as session:
            after = await get_generation_job(session, job_id)
            still_active = await has_active_job(session, repo_id)

        assert after.updated_at > before, "heartbeat did not advance around a to_thread call"
        assert still_active is True
    finally:
        await progress.drain_and_stop()


@pytest.mark.asyncio
async def test_heartbeat_cannot_survive_an_unoffloaded_blocking_call(session_factory):
    """Documents the actual hazard the PR review caught in
    knowledge_graph.skeleton: a blocking call made *directly* on the event
    loop starves the heartbeat no matter how it is implemented, because the
    loop cannot run any other task -- including the heartbeat's own timer --
    until the blocking call returns. This is a property of asyncio, not a
    gap in JobProgressCallback, which is exactly why the fix for every
    long synchronous phase is to wrap it in asyncio.to_thread rather than to
    lean on a cleverer heartbeat.
    """
    _repo_id, job_id = await _seed_running_job(session_factory)

    progress = JobProgressCallback(job_id, session_factory, heartbeat_interval_s=0.05)
    progress.start_heartbeat()

    async with session_factory() as session:
        before = (await get_generation_job(session, job_id)).updated_at

    # The bug: a blocking call made directly, not via to_thread. Stop the
    # loop immediately after so an overdue catch-up tick (its sleep deadline
    # passed while blocked) can't race the read below.
    time.sleep(0.3)
    await progress.drain_and_stop()

    async with session_factory() as session:
        after = (await get_generation_job(session, job_id)).updated_at
    assert after == before, (
        "heartbeat wrote during a blocking call -- if this starts failing, "
        "asyncio's scheduling guarantees changed and the to_thread "
        "requirement on every long synchronous phase needs re-examining"
    )


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
