"""Cancellation behaviour of ``_GenerationRun.run_level`` (issue #358).

A Ctrl+C during generation cancels the gather inside ``run_level``. Pages
still queued on the concurrency semaphore have never started their inner
coroutine; unless that coroutine object is explicitly closed, interpreter
shutdown emits one ``RuntimeWarning: coroutine ... was never awaited`` per
pending page. These tests pin the fix: after cancellation every inner
coroutine — running or queued — must be finalized.
"""

from __future__ import annotations

import asyncio
import gc
import inspect
import warnings
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from repowise.core.generation.models import GeneratedPage
from repowise.core.generation.page_generator.orchestrate import _GenerationRun


def _page(page_id: str) -> GeneratedPage:
    now = datetime.now(UTC).isoformat()
    return GeneratedPage(
        page_id=page_id,
        page_type="file_page",
        title=page_id,
        content=f"# {page_id}",
        source_hash="deadbeef",
        model_name="mock-model",
        provider_name="mock",
        input_tokens=1,
        output_tokens=1,
        cached_tokens=0,
        generation_level=2,
        target_path=page_id,
        created_at=now,
        updated_at=now,
    )


def _fake_run(max_concurrency: int = 1) -> SimpleNamespace:
    """Duck-typed ``_GenerationRun`` exposing only what ``run_level`` reads."""
    return SimpleNamespace(
        semaphore=asyncio.Semaphore(max_concurrency),
        job_system=None,
        job_id=None,
        on_page_done=None,
        on_page_ready=None,
        vector_store=None,
        completed_page_summaries={},
    )


async def _drain_cancelled_children() -> None:
    """Give the loop a few ticks so cancelled child tasks finalize."""
    for _ in range(5):
        await asyncio.sleep(0)


async def test_cancel_closes_queued_coroutines():
    """Coroutines still queued on the semaphore are closed, not leaked."""
    run = _fake_run(max_concurrency=1)
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocker() -> GeneratedPage:
        started.set()
        await release.wait()
        return _page("blocker")

    async def queued(i: int) -> GeneratedPage:
        return _page(f"queued-{i}")

    inner = [blocker()] + [queued(i) for i in range(5)]
    named_coros = [(f"pid-{i}", c) for i, c in enumerate(inner)]

    task = asyncio.ensure_future(_GenerationRun.run_level(run, named_coros, 2))
    await started.wait()  # blocker holds the only semaphore slot
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _drain_cancelled_children()

    # Every inner coroutine — the one mid-await and the five never-started
    # ones — must be finalized so no "never awaited" warning can fire.
    states = [inspect.getcoroutinestate(c) for c in inner]
    assert states == ["CORO_CLOSED"] * len(inner)


async def test_cancel_emits_no_never_awaited_warning():
    """End-to-end on the symptom: GC after cancel produces no RuntimeWarning."""
    run = _fake_run(max_concurrency=1)
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocker() -> GeneratedPage:
        started.set()
        await release.wait()
        return _page("blocker")

    async def queued(i: int) -> GeneratedPage:
        return _page(f"queued-{i}")

    named_coros = [("pid-b", blocker())] + [(f"pid-{i}", queued(i)) for i in range(5)]

    task = asyncio.ensure_future(_GenerationRun.run_level(run, named_coros, 2))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _drain_cancelled_children()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        del named_coros, task
        gc.collect()

    leaked = [
        w
        for w in caught
        if issubclass(w.category, RuntimeWarning) and "was never awaited" in str(w.message)
    ]
    assert leaked == []


async def test_run_level_completes_normally_after_fix():
    """Happy path untouched: pages come back, failures stay return-values."""
    run = _fake_run(max_concurrency=2)

    async def ok(i: int) -> GeneratedPage:
        await asyncio.sleep(0)
        return _page(f"ok-{i}")

    async def boom() -> GeneratedPage:
        raise ValueError("synthetic failure")

    named_coros = [(f"pid-{i}", ok(i)) for i in range(3)] + [("pid-boom", boom())]
    pages = await _GenerationRun.run_level(run, named_coros, 2)

    assert sorted(p.page_id for p in pages) == ["ok-0", "ok-1", "ok-2"]
    # The failing page's summary was never captured, the others' were.
    assert set(run.completed_page_summaries) == {"ok-0", "ok-1", "ok-2"}
