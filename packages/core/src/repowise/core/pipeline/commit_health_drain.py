"""Drain the commit-health backlog in a detached process, off the update's clock.

An update scans commits inline for :data:`~.commit_health.UPDATE_BUDGET_SECONDS`
and hands the rest here. The drain waits until no update or init is running,
takes the per-repo scan lock, and scans in short rounds up to the full
:data:`~.commit_health.DEFAULT_BUDGET_SECONDS`. Between rounds it stops if an
update has started: that update scans the newest commits itself and starts a
fresh drain if it leaves any, so the two never compete for the CPU or the
store. Each round writes its own rows, so a killed drain loses one round at most.

Run as ``python -m repowise.core.pipeline.commit_health_drain <repo_path> <repo_id>``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
from pathlib import Path

#: Set to ``0`` to keep every scan inline (the test suite does).
BACKGROUND_ENV = "REPOWISE_COMMIT_HEALTH_BACKGROUND"

#: Wall clock per round: how long an update that starts mid-drain can wait for the CPU.
ROUND_SECONDS = 30.0

#: How long a drain waits for its turn before giving up. The next update starts another.
WAIT_SECONDS = 3600.0
POLL_SECONDS = 2.0


def start_background_drain(repo_path: str, repo_id: str) -> None:
    """Start the drain detached; never raises, a missed drain is picked up by the next update."""
    if os.environ.get(BACKGROUND_ENV, "1") == "0":
        return
    from repowise.core.spawn import spawn_detached

    argv = [sys.executable, "-m", __name__, str(repo_path), repo_id]
    with contextlib.suppress(OSError):
        spawn_detached(argv, str(repo_path))


def _run_active(repo_path: Path) -> bool:
    from repowise.core.update_lock import read_init_lock, read_update_lock

    return read_update_lock(repo_path) is not None or read_init_lock(repo_path) is not None


def wait_for_turn(repo_path: Path, timeout: float = WAIT_SECONDS) -> bool:
    """Block until no update or init runs and the scan lock is ours; ``False`` on timeout."""
    from repowise.core.update_lock import try_acquire_commit_health_lock

    deadline = time.monotonic() + timeout
    while True:
        if not _run_active(repo_path) and try_acquire_commit_health_lock(repo_path) is None:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_SECONDS)


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
        await drain(create_session_factory(engine), repo_id, repo_path, total=budget_seconds())
    finally:
        await engine.dispose()


def main(argv: list[str]) -> int:
    from repowise.core.update_lock import release_commit_health_lock

    repo_path, repo_id = Path(argv[0]), argv[1]
    if not wait_for_turn(repo_path):
        return 0
    try:
        asyncio.run(_drain_store(repo_path, repo_id))
    except Exception:
        return 1
    finally:
        release_commit_health_lock(repo_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
