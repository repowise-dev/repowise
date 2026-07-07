"""Update-lock staleness: live-PID probe + PID-reuse identity check.

A crashed/killed ``repowise update`` (SIGKILL, power loss — paths atexit
can't cover) used to block further updates for the full 30-minute
wall-clock window because ``read_update_lock`` never validated that the
lock's PID was still alive. These tests pin the new semantics for both
the canonical CLI lock and its workspace mirror in
``repowise.core.workspace.update``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from repowise.cli import helpers
from repowise.core.procutils import process_create_token
from repowise.core.workspace import update as ws_update


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    return proc.pid


def _write_lock(repo: Path, payload: dict) -> None:
    (repo / ".repowise").mkdir(parents=True, exist_ok=True)
    (repo / ".repowise" / ".update.lock").write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# Canonical CLI lock (repowise.cli.helpers)
# ---------------------------------------------------------------------------


def test_acquire_records_pid_and_create_token(tmp_path: Path) -> None:
    assert helpers.try_acquire_update_lock(tmp_path, "abc123") is None

    payload = json.loads(
        (tmp_path / ".repowise" / ".update.lock").read_text(encoding="utf-8")
    )
    assert payload["pid"] == os.getpid()
    assert payload["target_commit"] == "abc123"
    assert payload["pid_create_token"] == process_create_token(os.getpid())


def test_fresh_lock_with_live_pid_is_honored(tmp_path: Path) -> None:
    assert helpers.try_acquire_update_lock(tmp_path, "abc123") is None

    payload = helpers.read_update_lock(tmp_path)
    assert payload is not None
    assert payload["pid"] == os.getpid()


def test_second_acquire_returns_live_owner(tmp_path: Path) -> None:
    """Check + acquire are one atomic step: a live lock blocks the second
    caller and hands back the owner's payload instead of overwriting."""
    assert helpers.try_acquire_update_lock(tmp_path, "first") is None

    blocked_by = helpers.try_acquire_update_lock(tmp_path, "second")
    assert blocked_by is not None
    assert blocked_by["target_commit"] == "first"
    # The original lock file was not clobbered by the losing caller.
    payload = helpers.read_update_lock(tmp_path)
    assert payload is not None
    assert payload["target_commit"] == "first"


def test_acquire_replaces_stale_lock(tmp_path: Path) -> None:
    """A dead owner's lock is cleared and the exclusive create retried."""
    _write_lock(
        tmp_path,
        {"pid": _dead_pid(), "target_commit": "crashed", "started_at": time.time()},
    )

    assert helpers.try_acquire_update_lock(tmp_path, "fresh") is None
    payload = helpers.read_update_lock(tmp_path)
    assert payload is not None
    assert payload["target_commit"] == "fresh"
    assert payload["pid"] == os.getpid()


def test_release_then_reacquire(tmp_path: Path) -> None:
    assert helpers.try_acquire_update_lock(tmp_path, "one") is None
    helpers.release_update_lock(tmp_path)
    assert helpers.try_acquire_update_lock(tmp_path, "two") is None


def test_fresh_lock_with_dead_pid_is_stale(tmp_path: Path) -> None:
    """The headline fix: a crashed update's lock no longer blocks for 30 min."""
    _write_lock(
        tmp_path,
        {"pid": _dead_pid(), "target_commit": "abc", "started_at": time.time()},
    )

    assert helpers.read_update_lock(tmp_path) is None


def test_lock_with_recycled_pid_is_stale(tmp_path: Path) -> None:
    """Same PID, different creation token ⇒ an unrelated process — stale."""
    _write_lock(
        tmp_path,
        {
            "pid": os.getpid(),
            "pid_create_token": "definitely-not-our-create-token",
            "target_commit": "abc",
            "started_at": time.time(),
        },
    )

    assert helpers.read_update_lock(tmp_path) is None


def test_legacy_lock_without_token_still_honored(tmp_path: Path) -> None:
    """Locks written by older repowise versions carry no token — the
    identity check is skipped, liveness + wall clock still apply."""
    _write_lock(
        tmp_path,
        {"pid": os.getpid(), "target_commit": "abc", "started_at": time.time()},
    )

    assert helpers.read_update_lock(tmp_path) is not None


def test_lock_without_pid_falls_back_to_wall_clock(tmp_path: Path) -> None:
    _write_lock(tmp_path, {"target_commit": "abc", "started_at": time.time()})
    assert helpers.read_update_lock(tmp_path) is not None

    _write_lock(
        tmp_path,
        {
            "target_commit": "abc",
            "started_at": time.time() - helpers.UPDATE_LOCK_STALE_AFTER_SECONDS - 60,
        },
    )
    assert helpers.read_update_lock(tmp_path) is None


def test_old_lock_is_stale_even_with_live_pid(tmp_path: Path) -> None:
    """A hung-but-alive update must still hit the wall-clock ceiling."""
    _write_lock(
        tmp_path,
        {
            "pid": os.getpid(),
            "pid_create_token": process_create_token(os.getpid()),
            "target_commit": "abc",
            "started_at": time.time() - helpers.UPDATE_LOCK_STALE_AFTER_SECONDS - 60,
        },
    )

    assert helpers.read_update_lock(tmp_path) is None


def test_unknown_probe_results_fall_back_to_wall_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When liveness can't be determined, a fresh lock must stay honored."""
    _write_lock(
        tmp_path,
        {"pid": os.getpid(), "target_commit": "abc", "started_at": time.time()},
    )
    monkeypatch.setattr("repowise.core.procutils.pid_alive", lambda _pid: None)

    assert helpers.read_update_lock(tmp_path) is not None


# ---------------------------------------------------------------------------
# Workspace path — must use the exact same shared core implementation
# ---------------------------------------------------------------------------


def test_workspace_uses_shared_core_lock(tmp_path: Path) -> None:
    from repowise.core.update_lock import release_update_lock, try_acquire_update_lock

    assert ws_update._try_acquire_lock is try_acquire_update_lock
    assert ws_update._release_lock is release_update_lock

    # Round-trip through the workspace aliases against the CLI view.
    assert ws_update._try_acquire_lock(tmp_path, "abc123") is None
    try:
        payload = helpers.read_update_lock(tmp_path)
        assert payload is not None
        assert payload["pid"] == os.getpid()
    finally:
        ws_update._release_lock(tmp_path)
