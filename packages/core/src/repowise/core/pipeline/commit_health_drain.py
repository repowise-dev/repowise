"""Drain the commit-health backlog in a detached process, off the update's clock.

An update scans commits inline for :data:`~.commit_health.UPDATE_BUDGET_SECONDS`
and hands the rest here. The drain waits until no update or init is running,
takes the per-repo scan lock, and scans in short rounds up to the full
:data:`~.commit_health.DEFAULT_BUDGET_SECONDS`. Between rounds it stops if an
update has started: that update scans the newest commits itself and starts a
fresh drain if it leaves any, so the two never compete for the CPU or the
store. Each round writes its own rows, so a killed drain loses one round at most.

Bounded on every axis. At most one drain runs and one waits per repo: a drain
that finds another already waiting exits at once, since the waiting one reads
the window when its turn comes. A drain that never gets its turn gives up after
:data:`WAIT_SECONDS`, and one that does exits after its budget plus
:data:`OVERRUN_SECONDS` whatever it is doing.

Run as ``python -m repowise.core.pipeline.commit_health_drain <repo_path> <repo_id>``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import threading
import time
from pathlib import Path

#: Set to ``0`` to keep every scan inline (the test suite does).
BACKGROUND_ENV = "REPOWISE_COMMIT_HEALTH_BACKGROUND"

#: Wall clock per round: how long an update that starts mid-drain can wait for the CPU.
ROUND_SECONDS = 30.0

#: How long a drain waits for its turn before giving up. The next update starts another.
WAIT_SECONDS = 3600.0
POLL_SECONDS = 2.0

#: Past its budget, how long a drain may run before it exits regardless. The
#: budget is checked between commits, so the last commit can overrun it: up to
#: the per-commit file cap at about a second a file, then the read-view rewrite.
#: Ceiling: a commit slower than this is cut off and retried by the next drain;
#: a deadline inside the analyzer would let it stop cleanly instead.
OVERRUN_SECONDS = 600.0


def background_enabled() -> bool:
    return os.environ.get(BACKGROUND_ENV, "1") != "0"


def start_background_drain(repo_path: str, repo_id: str) -> None:
    """Start the drain detached; never raises, a missed drain is picked up by the next update."""
    if not background_enabled():
        return
    from repowise.core.update_lock import read_drain_queue

    # One already waits, and it reads the window, these commits included, when its turn comes.
    if read_drain_queue(Path(repo_path)) is not None:
        return
    from repowise.core.spawn import spawn_detached

    # -P: the cwd is the user's repo, and a top-level module there must not shadow ours.
    argv = [sys.executable, "-P", "-m", __name__, str(repo_path), repo_id]
    with contextlib.suppress(OSError):
        spawn_detached(argv, str(repo_path))


def _run_active(repo_path: Path) -> bool:
    from repowise.core.update_lock import read_init_lock, read_update_lock

    return read_update_lock(repo_path) is not None or read_init_lock(repo_path) is not None


def wait_for_turn(repo_path: Path, timeout: float = WAIT_SECONDS) -> bool:
    """Block until no update or init runs and the scan lock is ours; ``False`` on timeout.

    Waits in the repo's one queue slot and frees it on return, so the next
    update can queue a drain behind this one. ``False`` at once when another
    drain already waits.
    """
    from repowise.core.update_lock import (
        release_drain_queue,
        try_acquire_commit_health_lock,
        try_acquire_drain_queue,
    )

    if try_acquire_drain_queue(repo_path) is not None:
        return False
    try:
        deadline = time.monotonic() + timeout
        while True:
            if not _run_active(repo_path) and try_acquire_commit_health_lock(repo_path) is None:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(POLL_SECONDS)
    finally:
        release_drain_queue(repo_path)


async def drain(
    session_factory: object,
    repo_id: str,
    repo_path: Path,
    *,
    total: float,
    round_seconds: float = ROUND_SECONDS,
) -> int:
    """Scan round after round until the window is covered, *total* is spent, or an update starts.

    Returns the number of rows stored. The caller holds the scan lock.
    """
    from .commit_health import scan_window

    stored = 0
    started = time.monotonic()
    while (left := total - (time.monotonic() - started)) > 0:
        stats = await scan_window(
            session_factory, repo_id, str(repo_path), min(round_seconds, left)
        )
        stored += stats["stored"]
        progressed = stats["scanned"] + stats["skipped"] > 0
        if not stats["exhausted"] or not progressed or _run_active(repo_path):
            break
    return stored


async def _drain_store(repo_path: Path, repo_id: str) -> None:
    from repowise.core.persistence import create_engine, create_session_factory
    from repowise.core.persistence.database import resolve_db_url

    from .commit_health import budget_seconds

    engine = create_engine(resolve_db_url(repo_path))
    try:
        factory = create_session_factory(engine)
        stored = await drain(factory, repo_id, repo_path, total=budget_seconds())
        # An update that started meanwhile writes the views itself, from its
        # final stores; views built here mid-update could outlive it.
        if stored and not _run_active(repo_path):
            await _rewrite_read_snapshots(factory, repo_id)
    finally:
        await engine.dispose()


async def _rewrite_read_snapshots(session_factory: object, repo_id: str) -> None:
    """Store the read views again: the rows the drain wrote are one of their inputs.

    Committing those rows dropped the views the update stored, so every new
    MCP or web process would rebuild them live until the next update.
    """
    from repowise.core.persistence.crud.analysis.actions import write_read_snapshots
    from repowise.core.persistence.database import get_session

    async with get_session(session_factory) as session:
        await write_read_snapshots(session, repo_id)


def _log(repo_path: Path, text: str) -> None:
    # Detached with stdio on DEVNULL, so anything worth reading goes to a file.
    try:
        with (repo_path / ".repowise" / "commit_health_drain.log").open("a", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass


def _log_failure(repo_path: Path) -> None:
    import traceback

    _log(repo_path, traceback.format_exc())


def _arm_deadline(repo_path: Path, seconds: float) -> threading.Timer:
    """Exit the process once *seconds* pass, wherever the scan is.

    The scan lock is left on disk: its owner is then dead, which every reader
    already treats as free. Rows land one transaction per round, so the exit
    loses the round in flight and nothing else.
    """

    def _expire() -> None:
        _log(repo_path, f"commit-health drain stopped at its {seconds:.0f} s cap")
        os._exit(0)

    timer = threading.Timer(seconds, _expire)
    timer.daemon = True
    timer.start()
    return timer


def main(argv: list[str]) -> int:
    from repowise.core.update_lock import release_commit_health_lock

    from .commit_health import budget_seconds

    repo_path, repo_id = Path(argv[0]), argv[1]
    if not wait_for_turn(repo_path):
        return 0
    deadline = _arm_deadline(repo_path, budget_seconds() + OVERRUN_SECONDS)
    try:
        asyncio.run(_drain_store(repo_path, repo_id))
    except Exception:
        _log_failure(repo_path)
        return 1
    finally:
        deadline.cancel()
        release_commit_health_lock(repo_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
