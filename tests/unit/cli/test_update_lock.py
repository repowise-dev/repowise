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

    payload = json.loads((tmp_path / ".repowise" / ".update.lock").read_text(encoding="utf-8"))
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


def test_loop_exhaustion_never_returns_acquired_without_a_lock_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: after two lost create-races against a stale lock, the
    caller must not be handed ``None`` ("acquired") with no lock file on disk.

    Both initial ``os.link`` calls raise ``FileExistsError`` against a stale
    lock, exactly the interleaving that used to fall through to a bare
    ``read_update_lock`` and return ``None`` with nothing on disk — letting a
    concurrent update race on the same index. The function must make one final
    exclusive create so ``None`` always implies the caller owns a lock.
    """
    _write_lock(
        tmp_path,
        {"pid": _dead_pid(), "target_commit": "crashed", "started_at": time.time()},
    )

    real_link = os.link
    link_calls = 0

    def _failing_link(src: str | os.PathLike, dst: str | os.PathLike) -> None:
        nonlocal link_calls
        link_calls += 1
        if link_calls <= 2:
            raise FileExistsError(f"[Errno 17] File exists: '{dst}'")
        real_link(src, dst)

    monkeypatch.setattr(os, "link", _failing_link)

    assert helpers.try_acquire_update_lock(tmp_path, "fresh") is None
    assert (tmp_path / ".repowise" / ".update.lock").exists()
    payload = helpers.read_update_lock(tmp_path)
    assert payload is not None
    assert payload["target_commit"] == "fresh"
    assert payload["pid"] == os.getpid()
    assert link_calls == 3


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


def test_old_lock_with_live_pid_is_honored(tmp_path: Path) -> None:
    """Age alone never evicts an owner we can positively see running.

    The wall clock cannot tell a slow full update on a large repo apart from a
    wedged one, and guessing wrong puts two updates on one index, both writing
    the same state and the same page rows. Liveness is the better evidence, so
    it wins; a long-held live lock is reported to the user instead.
    """
    _write_lock(
        tmp_path,
        {
            "pid": os.getpid(),
            "pid_create_token": process_create_token(os.getpid()),
            "target_commit": "abc",
            "started_at": time.time() - helpers.UPDATE_LOCK_STALE_AFTER_SECONDS - 60,
        },
    )

    payload = helpers.read_update_lock(tmp_path)
    assert payload is not None
    assert payload["target_commit"] == "abc"


def test_old_lock_with_dead_pid_is_still_stale(tmp_path: Path) -> None:
    """Honouring live owners must not resurrect dead ones."""
    _write_lock(
        tmp_path,
        {
            "pid": _dead_pid(),
            "target_commit": "abc",
            "started_at": time.time() - helpers.UPDATE_LOCK_STALE_AFTER_SECONDS - 60,
        },
    )

    assert helpers.read_update_lock(tmp_path) is None


def test_a_live_owner_is_never_evicted_by_a_contender(tmp_path: Path) -> None:
    """The end-to-end consequence: no acquire can steal from a live owner."""
    _write_lock(
        tmp_path,
        {
            "pid": os.getpid(),
            "pid_create_token": process_create_token(os.getpid()),
            "target_commit": "wedged",
            "started_at": time.time() - 9 * 3600,
        },
    )

    blocked_by = helpers.try_acquire_update_lock(tmp_path, "contender")
    assert blocked_by is not None
    assert blocked_by["target_commit"] == "wedged"


# ---------------------------------------------------------------------------
# Age reporting — the half of a wedged update the user can act on
# ---------------------------------------------------------------------------


def test_lock_age_reads_started_at() -> None:
    from repowise.core.update_lock import lock_age_seconds

    assert lock_age_seconds({"started_at": time.time() - 120}) == pytest.approx(120, abs=5)
    assert lock_age_seconds({}) is None
    assert lock_age_seconds(None) is None
    assert lock_age_seconds({"started_at": "not-a-number"}) is None


def test_lock_age_never_reports_negative() -> None:
    """A clock that moved backwards must not print a negative age."""
    from repowise.core.update_lock import lock_age_seconds

    assert lock_age_seconds({"started_at": time.time() + 600}) == 0.0


def test_format_lock_age_coarsens_with_age() -> None:
    """The nine-hour case has to read as hours, not as a five-digit second count."""
    from repowise.core.update_lock import format_lock_age

    assert format_lock_age(12) == "for 12s"
    assert format_lock_age(20 * 60) == "for 20m"
    assert format_lock_age(9 * 3600) == "for 9.0h"
    assert format_lock_age(None) == "for an unknown time"


def test_suspect_threshold_flags_a_long_held_lock() -> None:
    from repowise.core.update_lock import UPDATE_LOCK_SUSPECT_AFTER_SECONDS

    assert 60 < UPDATE_LOCK_SUSPECT_AFTER_SECONDS < 9 * 3600


def test_deferred_repo_result_carries_the_lock_age(tmp_path: Path) -> None:
    """The workspace summary can only report the age if the result carries it."""
    from repowise.core.workspace.update import RepoUpdateResult

    assert RepoUpdateResult(alias="a", updated=False).lock_age_seconds is None
    assert (
        RepoUpdateResult(alias="a", updated=False, lock_age_seconds=32400.0).lock_age_seconds
        == 32400.0
    )


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


# ---------------------------------------------------------------------------
# Workspace-level lock — the #1831 single-flight guard
# ---------------------------------------------------------------------------


def test_workspace_lock_lives_under_workspace_data_dir(tmp_path: Path) -> None:
    """The workspace guard must NOT reuse the per-repo lock path — it lives in
    ``.repowise-workspace/.update.lock`` so a workspace's members share one."""
    from repowise.core.update_lock import (
        update_workspace_lock,
        workspace_update_lock_path,
    )

    path = workspace_update_lock_path(tmp_path)
    assert path == tmp_path / ".repowise-workspace" / ".update.lock"

    assert update_workspace_lock(tmp_path) is None
    try:
        assert (tmp_path / ".repowise-workspace" / ".update.lock").exists()
        # The per-repo lock is a different file — a held workspace lock does
        # not block a single-repo update.
        assert not (tmp_path / ".repowise" / ".update.lock").exists()
    finally:
        from repowise.core.update_lock import release_workspace_lock

        release_workspace_lock(tmp_path)
    assert not (tmp_path / ".repowise-workspace" / ".update.lock").exists()


def test_workspace_lock_is_single_flight(tmp_path: Path) -> None:
    """A second workspace update defers to the first instead of running a
    redundant full pass."""
    from repowise.core.update_lock import (
        release_workspace_lock,
        update_workspace_lock,
    )

    assert update_workspace_lock(tmp_path) is None
    try:
        owner = update_workspace_lock(tmp_path)
        assert owner is not None
        assert owner["pid"] == os.getpid()
    finally:
        release_workspace_lock(tmp_path)

    # After release a fresh acquire wins again.
    assert update_workspace_lock(tmp_path) is None
    release_workspace_lock(tmp_path)


def test_stale_workspace_lock_is_cleared(tmp_path: Path) -> None:
    """A dead owner's workspace lock must not wedge the next update."""
    from repowise.core.update_lock import update_workspace_lock
    from repowise.core.workspace.update import _release_workspace_lock

    ws_dir = tmp_path / ".repowise-workspace"
    ws_dir.mkdir(parents=True, exist_ok=True)
    (ws_dir / ".update.lock").write_text(
        json.dumps({"pid": _dead_pid(), "target_commit": None, "started_at": time.time()}),
        encoding="utf-8",
    )

    assert update_workspace_lock(tmp_path) is None
    try:
        assert (ws_dir / ".update.lock").exists()
    finally:
        _release_workspace_lock(tmp_path)
