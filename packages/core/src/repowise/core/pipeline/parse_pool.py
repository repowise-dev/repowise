"""A spawn process pool where no single file can stall or exhaust the parse.

Every task gets a deadline (``REPOWISE_PARSE_TIMEOUT_S``). A thread cannot be
killed, so a task past its deadline is stopped by killing the pool's worker
processes: the late task is recorded as :class:`ParseTimeoutError`, the other
tasks that were in flight go back to the front of the queue, and a fresh pool
carries on. A worker that dies on its own (a native crash, an out-of-memory
kill) breaks the pool too. Its in-flight tasks are then retried one at a time,
so the one that crashes alone is recorded as :class:`ParseWorkerCrashError` and
the rest finish normally.

On Linux each worker's data segment is also capped
(``REPOWISE_PARSE_WORKER_MEMORY_MB``), so a runaway allocation becomes a
``MemoryError`` for that file instead of memory pressure on the host. Other
platforms have no per-process cap that covers ``mmap``; the deadline is their
only bound.
"""

from __future__ import annotations

import contextlib
import multiprocessing
import os
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "PENDING",
    "ParseTimeoutError",
    "ParseWorkerCrashError",
    "parse_timeout_s",
    "run_with_deadline",
]

# Force ``spawn`` for the parse pool instead of the POSIX default ``fork``.
# A workspace init generates docs for one repo (which imports and connects
# LanceDB) before ingesting the next, and ``lance`` is not fork-safe — it
# registers an ``os.register_at_fork`` hook that resets its native async
# runtime in the child but can still deadlock. Forking the parse workers
# after LanceDB is live is exactly what hung multi-repo inits at "Parsing
# files" (issue #678). ``spawn`` starts clean workers that never inherit the
# runtime; it is already the default on Windows/macOS.
MP_SPAWN = multiprocessing.get_context("spawn")

_TIMEOUT_ENV = "REPOWISE_PARSE_TIMEOUT_S"
# Normal files parse in well under a second, and the traverser caps parsed
# sources at 2 MB, so a minute is far past any legitimate parse even on a slow,
# shared CPU. It still stops a looping parser before it can grow without bound.
_DEFAULT_TIMEOUT_S = 60.0

_MEMORY_ENV = "REPOWISE_PARSE_WORKER_MEMORY_MB"
# A worker holds ~50 MB of grammars plus one file's tree; 2 GB is an order of
# magnitude above the largest parse and still bounded on a small host.
_DEFAULT_MEMORY_MB = 2048


# How long a fresh pool may take to run a no-op before it counts as unusable.
_STARTUP_TIMEOUT_S = 120


class ParseTimeoutError(Exception):
    """A file's parse ran past the deadline; its worker was killed."""


class ParseWorkerCrashError(Exception):
    """A file's worker process died while parsing it, even when run alone."""


class _Pending:
    def __repr__(self) -> str:
        return "PENDING"


# Placeholder in a results list for a task that has no outcome yet.
PENDING: Any = _Pending()


def _env_number(name: str, default: float) -> float:
    """A non-negative number from *name*; unset or empty means *default*."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = -1.0
    if value < 0:
        logger.warning("invalid_parse_pool_env", name=name, value=raw)
        return default
    return value


def parse_timeout_s() -> float | None:
    """Per-file parse deadline in seconds; ``REPOWISE_PARSE_TIMEOUT_S=0`` turns it off."""
    value = _env_number(_TIMEOUT_ENV, _DEFAULT_TIMEOUT_S)
    return value or None


def _worker_memory_bytes() -> int:
    """Per-worker data-segment cap; ``REPOWISE_PARSE_WORKER_MEMORY_MB=0`` turns it off."""
    return int(_env_number(_MEMORY_ENV, _DEFAULT_MEMORY_MB) * 1024 * 1024)


def _limit_worker_memory(limit_bytes: int) -> None:
    """Pool initializer: cap this worker's data segment where the OS enforces it.

    ``RLIMIT_DATA`` rather than ``RLIMIT_AS``: since Linux 4.7 it counts the
    private writable mappings malloc and Python's arenas live in, but not the
    address space that shared libraries and guard reservations take, so a
    normal worker is nowhere near it. Never raises: a worker without the cap
    still parses.
    """
    if not limit_bytes:
        return
    try:
        import resource
    except ImportError:  # Windows has no rlimits
        return
    # Platforms without RLIMIT_DATA, or a hard limit we may not raise, leave
    # the worker uncapped.
    with contextlib.suppress(AttributeError, ValueError, OSError):
        _soft, hard = resource.getrlimit(resource.RLIMIT_DATA)
        if hard != resource.RLIM_INFINITY:
            limit_bytes = min(limit_bytes, hard)
        resource.setrlimit(resource.RLIMIT_DATA, (limit_bytes, hard))


def _new_pool(workers: int) -> ProcessPoolExecutor:
    return ProcessPoolExecutor(
        max_workers=workers,
        mp_context=MP_SPAWN,
        initializer=_limit_worker_memory,
        initargs=(_worker_memory_bytes(),),
    )


def _kill_pool(pool: ProcessPoolExecutor) -> None:
    """Stop *pool* now, including a worker stuck in a task."""
    # ``kill_workers`` is 3.14+; before that the process map is the only handle.
    procs = list((getattr(pool, "_processes", None) or {}).values())
    for proc in procs:
        with contextlib.suppress(Exception):
            proc.kill()
    pool.shutdown(wait=False, cancel_futures=True)
    for proc in procs:
        with contextlib.suppress(Exception):
            proc.join(timeout=5)


class _Run:
    """State of one :func:`run_with_deadline` call across pool rebuilds."""

    def __init__(
        self,
        fn: Callable[[Any], Any],
        items: list[Any],
        results: list[Any],
        timeout: float | None,
        on_done: Callable[[int, Any], None] | None,
    ) -> None:
        self.fn = fn
        self.items = items
        self.results = results
        self.timeout = timeout
        self.on_done = on_done
        self.queue: deque[int] = deque(i for i, r in enumerate(results) if r is PENDING)
        # Tasks that were in flight when a worker died, retried one at a time.
        self.suspects: deque[int] = deque()
        self.pool_has_run = False

    def finish(self, idx: int, result: Any) -> None:
        self.results[idx] = result
        if self.on_done is not None:
            self.on_done(idx, result)

    def wait_budget(self, in_flight: dict[Future, tuple[int, float]]) -> float | None:
        if self.timeout is None:
            return None
        oldest = min(started for _idx, started in in_flight.values())
        return max(0.05, oldest + self.timeout - time.monotonic())

    def expire(self, in_flight: dict[Future, tuple[int, float]]) -> bool:
        """Record every task past its deadline; True when any was."""
        if self.timeout is None:
            return False
        now = time.monotonic()
        late = [f for f, (_i, started) in in_flight.items() if now - started >= self.timeout]
        for fut in late:
            idx, _started = in_flight.pop(fut)
            self.finish(idx, ParseTimeoutError(f"parse timed out after {self.timeout:g}s"))
        return bool(late)

    def collect(self, done: set[Future], in_flight: dict[Future, tuple[int, float]]) -> bool:
        """Record finished tasks; True when the pool broke under one of them."""
        broken = False
        for fut in done:
            exc = fut.exception()
            if isinstance(exc, BrokenProcessPool):
                broken = True
                continue
            idx, _started = in_flight.pop(fut)
            self.finish(idx, exc if exc is not None else fut.result())
        return broken

    def requeue(self, in_flight: dict[Future, tuple[int, float]], *, suspect: bool) -> None:
        """Send unfinished in-flight tasks back to the front of their queue."""
        target = self.suspects if suspect else self.queue
        target.extendleft(sorted((i for i, _s in in_flight.values()), reverse=True))
        in_flight.clear()

    def dispatch(
        self,
        pool: ProcessPoolExecutor,
        queue: deque[int],
        in_flight: dict[Future, tuple[int, float]],
        limit: int,
    ) -> None:
        # In flight never exceeds the worker count, so every in-flight task is
        # running and its clock is its own parse time.
        while queue and len(in_flight) < limit:
            idx = queue.popleft()
            in_flight[pool.submit(self.fn, self.items[idx])] = (idx, time.monotonic())

    def start(self, pool: ProcessPoolExecutor) -> None:
        """Run a no-op in *pool*; raise when it cannot, so the caller falls back.

        A pool that cannot start (spawn or import failure) is the
        environment's fault, not a file's. Suspects are the exception: they
        were in flight when a worker died, so they never go to an in-process
        fallback, where a native crash would take the whole run down.
        """
        try:
            pool.submit(os.getpid).result(timeout=_STARTUP_TIMEOUT_S)
        except Exception:
            if self.pool_has_run:
                while self.suspects:
                    self.finish(self.suspects.popleft(), ParseWorkerCrashError("parse worker crashed"))
            raise
        self.pool_has_run = True

    def round(self, workers: int) -> None:
        """Run tasks in one pool until the queue drains or the pool must go."""
        solo = bool(self.suspects)
        queue = self.suspects if solo else self.queue
        limit = 1 if solo else workers
        pool = _new_pool(limit)
        in_flight: dict[Future, tuple[int, float]] = {}
        clean = False
        try:
            self.start(pool)
            while queue or in_flight:
                self.dispatch(pool, queue, in_flight, limit)
                done, _ = wait(in_flight, self.wait_budget(in_flight), FIRST_COMPLETED)
                if self.collect(done, in_flight):
                    self.on_broken(in_flight, solo)
                    return
                if self.expire(in_flight):
                    self.requeue(in_flight, suspect=solo)
                    return
            clean = True
        finally:
            if clean:
                pool.shutdown(wait=True)
            else:
                _kill_pool(pool)

    def on_broken(self, in_flight: dict[Future, tuple[int, float]], solo: bool) -> None:
        if solo:
            # Alone in its pool, so this task is the one that killed it.
            (idx, _started) = next(iter(in_flight.values()))
            in_flight.clear()
            self.finish(idx, ParseWorkerCrashError("parse worker crashed"))
        else:
            self.requeue(in_flight, suspect=True)


def run_with_deadline(
    fn: Callable[[Any], Any],
    items: list[Any],
    workers: int,
    results: list[Any],
    *,
    timeout: float | None,
    on_done: Callable[[int, Any], None] | None = None,
) -> None:
    """Run ``fn(item)`` for each :data:`PENDING` slot of *results* in a spawn pool.

    Fills *results* in place, aligned with *items*: the return value, the
    exception the task raised, or a :class:`ParseTimeoutError` /
    :class:`ParseWorkerCrashError` instance. ``on_done(index, result)`` runs in
    the calling thread as each task resolves. A pool that cannot start raises,
    with every slot it did not resolve left :data:`PENDING` for the caller's
    fallback; slots that timed out or crashed are resolved, so a fallback
    never runs those files again.
    """
    run = _Run(fn, items, results, timeout, on_done)
    while run.queue or run.suspects:
        run.round(workers)
