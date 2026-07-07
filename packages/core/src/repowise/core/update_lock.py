"""Per-repo update lock — single-flight guard for ``repowise update``.

One implementation shared by the CLI update command (``cli/helpers.py``
re-exports these) and the core workspace updater, which previously carried a
hand-synced copy. The lock file records the owning PID, its creation-time
token, and the target commit so readers can tell a live update apart from a
crashed one (and the augment hook can suppress redundant stale-wiki warnings).
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any

UPDATE_LOCK_FILENAME = ".update.lock"

# Locks older than this are considered stale (a crashed update); the hook
# will ignore them and the next update will overwrite. Generous enough to
# cover a slow full-update on a large repo.
UPDATE_LOCK_STALE_AFTER_SECONDS = 30 * 60


def update_lock_path(repo_path: Path) -> Path:
    return Path(repo_path) / ".repowise" / UPDATE_LOCK_FILENAME


def acquire_update_lock(repo_path: Path, target_commit: str | None) -> Path:
    """Write the update lock file. Returns its path.

    The lock contains the PID and target commit so the augment hook can
    decide whether a stale-wiki warning is redundant, plus the writing
    process's creation-time token so ``read_update_lock`` can tell a live
    lock owner apart from an unrelated process that recycled the PID.
    Best-effort: if write fails (read-only fs, permissions), returns the
    path anyway — callers must still call ``release_update_lock`` in a
    finally block.
    """
    from repowise.core.procutils import process_create_token

    lock_path = update_lock_path(repo_path)
    payload = {
        "pid": os.getpid(),
        "pid_create_token": process_create_token(os.getpid()),
        "target_commit": target_commit,
        "started_at": time.time(),
    }
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass
    return lock_path


def release_update_lock(repo_path: Path) -> None:
    """Remove the update lock file. Safe to call if it doesn't exist."""
    with contextlib.suppress(OSError):
        update_lock_path(repo_path).unlink(missing_ok=True)


def read_update_lock(repo_path: Path) -> dict[str, Any] | None:
    """Return the lock payload if present and not stale, else ``None``.

    A lock is stale when its wall-clock age exceeds
    ``UPDATE_LOCK_STALE_AFTER_SECONDS`` (a hung-but-alive update must not
    block forever) — or, much sooner, when its owning PID is positively
    dead or has been recycled by an unrelated process. The PID probe means
    a crashed/killed update (SIGKILL, power loss — paths atexit can't
    cover) no longer blocks further updates for the full 30-minute window.
    Probes that can't decide ("unknown") fall back to the wall clock, so a
    live update is never treated as stale by mistake.
    """
    from repowise.core.procutils import pid_alive, process_create_token

    lock_path = update_lock_path(repo_path)
    if not lock_path.exists():
        return None
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    started = payload.get("started_at")
    if not isinstance(started, (int, float)):
        return None
    if time.time() - started > UPDATE_LOCK_STALE_AFTER_SECONDS:
        return None

    pid = payload.get("pid")
    if isinstance(pid, int) and pid > 0:
        alive = pid_alive(pid)
        if alive is False:
            return None
        if alive is True:
            stored_token = payload.get("pid_create_token")
            # Legacy locks (pre-token) skip the identity check and rely on
            # liveness + wall clock alone.
            if isinstance(stored_token, str) and stored_token:
                current_token = process_create_token(pid)
                if current_token is not None and current_token != stored_token:
                    return None
    return payload
