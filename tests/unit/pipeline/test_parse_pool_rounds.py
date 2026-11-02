"""``run_with_deadline``'s scheduling, driven in-process with a fake executor.

The fake resolves futures from timer threads, so deadlines, requeues and
crash isolation run in this process where coverage and timing can see them.
``test_parse_pool_deadline.py`` covers the same behaviour with real workers.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool

import pytest

from repowise.core.pipeline import parse_pool
from repowise.core.pipeline.parse_pool import (
    PENDING,
    ParseTimeoutError,
    ParseWorkerCrashError,
    run_with_deadline,
)


def _upper(item: str) -> str:
    return item.upper()


class _FakePool:
    """An executor whose tasks behave by item name.

    ``hang`` never finishes; ``crash`` breaks the pool, failing every
    unfinished future with ``BrokenProcessPool``; ``victim`` never finishes in
    the first pool built and finishes normally in later ones; ``slow``
    finishes after a short delay; anything else finishes right away.
    """

    def __init__(self, generation: int) -> None:
        self.generation = generation
        self.pending: list[Future] = []
        self.killed = False

    def submit(self, fn, item=None):
        fut: Future = Future()
        if fn is os.getpid:
            fut.set_result(0)
            return fut
        self.pending.append(fut)
        if item == "hang" or (item == "victim" and self.generation == 0):
            return fut
        if item == "crash":
            for other in self.pending:
                if not other.done():
                    other.set_exception(BrokenProcessPool("worker died"))
            return fut
        delay = 0.3 if item == "slow" else 0.01
        threading.Timer(delay, self._finish, (fut, fn, item)).start()
        return fut

    @staticmethod
    def _finish(fut: Future, fn, item) -> None:
        if not fut.done():
            fut.set_result(fn(item))

    def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
        self.killed = not wait


@pytest.fixture
def pools(monkeypatch) -> list[tuple[int, _FakePool]]:
    built: list[tuple[int, _FakePool]] = []

    def new_pool(workers: int) -> _FakePool:
        pool = _FakePool(len(built))
        built.append((workers, pool))
        return pool

    monkeypatch.setattr(parse_pool, "_new_pool", new_pool)
    return built


def _run(items: list[str], workers: int, timeout: float | None) -> list:
    results = [PENDING] * len(items)
    run_with_deadline(_upper, items, workers, results, timeout=timeout)
    return results


def test_timeout_requeues_the_other_in_flight_task(pools) -> None:
    # ``victim`` is still running in the first pool when ``hang`` expires, so
    # it is requeued, not blamed, and finishes in the replacement pool.
    results = _run(["hang", "a", "victim", "b"], workers=2, timeout=0.5)

    assert isinstance(results[0], ParseTimeoutError)
    assert str(results[0]) == "parse timed out after 0.5s"
    assert results[1:] == ["A", "VICTIM", "B"]
    assert len(pools) == 2
    assert pools[0][1].killed, "the pool holding the stuck task is killed, not awaited"


def test_crash_is_blamed_only_after_a_one_at_a_time_retry(pools) -> None:
    results = _run(["slow", "crash", "c"], workers=2, timeout=5.0)

    assert results[0] == "SLOW", "the task that was in flight beside the crash is retried"
    assert isinstance(results[1], ParseWorkerCrashError)
    assert results[2] == "C"
    # The shared pool breaks; a one-worker pool runs the two suspects in turn
    # until ``crash`` breaks it alone; a fresh shared pool finishes the rest.
    assert [workers for workers, _pool in pools] == [2, 1, 2]


def test_no_deadline_waits_for_a_slow_task(pools) -> None:
    results = _run(["slow", "a"], workers=1, timeout=None)
    assert results == ["SLOW", "A"]
    assert len(pools) == 1


def test_zero_timeout_env_means_no_deadline(monkeypatch, pools) -> None:
    monkeypatch.setenv("REPOWISE_PARSE_TIMEOUT_S", "0")
    results = _run(["slow"], workers=1, timeout=parse_pool.parse_timeout_s())
    assert results == ["SLOW"]


def test_a_failed_probe_after_a_crash_never_sends_suspects_to_the_fallback(
    monkeypatch, pools
) -> None:
    real_new_pool = parse_pool._new_pool

    class _NoStart(_FakePool):
        def submit(self, fn, item=None):
            if fn is os.getpid:
                raise OSError("cannot spawn")
            return super().submit(fn, item)

    def new_pool(workers: int):
        pool = real_new_pool(workers)
        return pool if len(pools) == 1 else _NoStart(pool.generation)

    monkeypatch.setattr(parse_pool, "_new_pool", new_pool)
    results = [PENDING] * 3
    with pytest.raises(OSError, match="cannot spawn"):
        run_with_deadline(_upper, ["slow", "crash", "c"], 2, results, timeout=5.0)

    # Both suspects are resolved as crashes; only the never-started file is
    # left for the in-process fallback.
    assert isinstance(results[0], ParseWorkerCrashError)
    assert isinstance(results[1], ParseWorkerCrashError)
    assert results[2] is PENDING
