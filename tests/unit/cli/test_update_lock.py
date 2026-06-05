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
    helpers.acquire_update_lock(tmp_path, "abc123")

    payload = json.loads(
        (tmp_path / ".repowise" / ".update.lock").read_text(encoding="utf-8")
    )
    assert payload["pid"] == os.getpid()
    assert payload["target_commit"] == "abc123"
    assert payload["pid_create_token"] == process_create_token(os.getpid())


def test_fresh_lock_with_live_pid_is_honored(tmp_path: Path) -> None:
    helpers.acquire_update_lock(tmp_path, "abc123")

    payload = helpers.read_update_lock(tmp_path)
    assert payload is not None
    assert payload["pid"] == os.getpid()


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
# Workspace mirror (repowise.core.workspace.update) — format must stay in sync
# ---------------------------------------------------------------------------


def test_workspace_lock_round_trip_matches_cli_format(tmp_path: Path) -> None:
    ws_update._acquire_lock(tmp_path, "abc123")
    try:
        cli_view = helpers.read_update_lock(tmp_path)
        ws_view = ws_update._read_lock(tmp_path)
        assert cli_view is not None
        assert ws_view is not None
        assert cli_view["pid"] == ws_view["pid"] == os.getpid()
        assert cli_view["pid_create_token"] == ws_view["pid_create_token"]
    finally:
        ws_update._release_lock(tmp_path)


def test_workspace_lock_with_dead_pid_is_stale(tmp_path: Path) -> None:
    _write_lock(
        tmp_path,
        {"pid": _dead_pid(), "target_commit": "abc", "started_at": time.time()},
    )

    assert ws_update._read_lock(tmp_path) is None


def test_workspace_legacy_lock_without_token_still_honored(tmp_path: Path) -> None:
    _write_lock(
        tmp_path,
        {"pid": os.getpid(), "target_commit": "abc", "started_at": time.time()},
    )

    assert ws_update._read_lock(tmp_path) is not None
