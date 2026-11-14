"""A detached drain is bounded: one runs and one waits per repo, and each has a hard cap."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import repowise.core.pipeline.commit_health_drain as drain_mod
import repowise.core.spawn as spawn_mod
from repowise.core.update_lock import (
    read_drain_queue,
    release_commit_health_lock,
    release_drain_queue,
    release_update_lock,
    try_acquire_commit_health_lock,
    try_acquire_drain_queue,
    try_acquire_update_lock,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".repowise").mkdir()
    return tmp_path


def test_a_burst_of_updates_queues_one_drain(repo, monkeypatch) -> None:
    """Every update after the first finds a drain already waiting and spawns nothing."""
    spawned: list = []
    monkeypatch.setattr(spawn_mod, "spawn_detached", lambda argv, cwd: spawned.append(argv))
    monkeypatch.setenv(drain_mod.BACKGROUND_ENV, "1")
    assert try_acquire_drain_queue(repo) is None
    try:
        for _ in range(5):
            drain_mod.start_background_drain(str(repo), "rid")
    finally:
        release_drain_queue(repo)
    assert spawned == []

    drain_mod.start_background_drain(str(repo), "rid")
    assert len(spawned) == 1


def test_a_drain_that_finds_one_waiting_exits_at_once(repo) -> None:
    """Spawned anyway (two updates racing the check): it leaves without polling."""
    assert try_acquire_drain_queue(repo) is None
    try:
        started = time.monotonic()
        assert drain_mod.wait_for_turn(repo, timeout=60) is False
        assert time.monotonic() - started < 5
        # The waiting drain still holds its slot.
        assert read_drain_queue(repo) is not None
    finally:
        release_drain_queue(repo)


def test_the_slot_frees_once_the_drain_has_its_turn(repo) -> None:
    """The running drain is not the waiting one, so the next update can queue another."""
    assert drain_mod.wait_for_turn(repo, timeout=0) is True
    try:
        assert read_drain_queue(repo) is None
        assert try_acquire_commit_health_lock(repo) is not None
    finally:
        release_commit_health_lock(repo)


def test_a_drain_that_gives_up_frees_the_slot(repo) -> None:
    assert try_acquire_update_lock(repo, None) is None
    try:
        assert drain_mod.wait_for_turn(repo, timeout=0) is False
        assert read_drain_queue(repo) is None
    finally:
        release_update_lock(repo)


_STUCK = """
import asyncio, sys
import repowise.core.pipeline.commit_health_drain as drain_mod

async def stuck(*_a, **_k):
    await asyncio.sleep(120)

drain_mod._drain_store = stuck
drain_mod.OVERRUN_SECONDS = 1.0
sys.exit(drain_mod.main([sys.argv[1], "rid"]))
"""


def test_a_stuck_drain_exits_at_its_hard_cap(repo) -> None:
    """Past budget plus overrun the process exits, and its scan lock reads as free."""
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(sys.path),
        "REPOWISE_COMMIT_HEALTH_BUDGET_SECONDS": "0",
    }
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-c", _STUCK, str(repo)], env=env, timeout=60, capture_output=True
    )
    assert proc.returncode == 0, proc.stderr
    assert time.monotonic() - started < 30
    assert "cap" in (repo / ".repowise" / "commit_health_drain.log").read_text(encoding="utf-8")
    assert try_acquire_commit_health_lock(repo) is None
    release_commit_health_lock(repo)
