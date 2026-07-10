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
import threading
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


def try_acquire_update_lock(repo_path: Path, target_commit: str | None) -> dict[str, Any] | None:
    """Atomically acquire the update lock. ``None`` means acquired.

    Returns the live owner's payload when another update already holds the
    lock, so the caller can report who it lost to and bail. The payload is
    written to a private temp file and hard-linked into place, so the lock
    only ever becomes visible with its full content — an exclusive create
    followed by a write leaves a window where a contender reads the still
    empty file, mistakes it for a corrupt lock, and deletes the winner's
    live lock (two "winners"). A stale lock (dead or recycled PID, or past
    the wall-clock ceiling) is cleared and the create retried.

    The payload contains the PID and target commit so the augment hook can
    decide whether a stale-wiki warning is redundant, plus the writing
    process's creation-time token so ``read_update_lock`` can tell a live
    lock owner apart from an unrelated process that recycled the PID.
    Best-effort: unexpected ``OSError`` (read-only fs, permissions) counts
    as acquired — the lock is advisory and must never block an update.
    Callers must still call ``release_update_lock`` in a finally block.
    """
    from repowise.core.procutils import process_create_token

    lock_path = update_lock_path(repo_path)
    payload = {
        "pid": os.getpid(),
        "pid_create_token": process_create_token(os.getpid()),
        "target_commit": target_commit,
        "started_at": time.time(),
    }
    data = json.dumps(payload)
    tmp_path = lock_path.with_name(
        f"{UPDATE_LOCK_FILENAME}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    for _ in range(2):
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(data, encoding="utf-8")
            # Atomic create-with-content: the lock either doesn't exist or
            # holds a complete payload — contenders can never read a
            # half-written file.
            os.link(tmp_path, lock_path)
        except FileExistsError:
            existing = read_update_lock(repo_path)
            if existing is not None:
                return existing
            # Stale lock: clear it and retry the exclusive create.
            with contextlib.suppress(OSError):
                lock_path.unlink(missing_ok=True)
            continue
        except OSError:
            return None
        finally:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)
        return None
    # Lost the create race twice in a row: someone else just acquired a
    # fresh lock — report it. A still-unreadable lock degrades to acquired.
    return read_update_lock(repo_path)


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
