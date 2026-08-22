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

# Wall-clock ceiling, applied only when the owner's liveness cannot be
# established: no PID in the payload (legacy writer) or a probe that could not
# decide. A lock this old whose owner we cannot find is treated as abandoned.
#
# It is deliberately NOT applied to an owner we can positively see running.
# Breaking a live owner's lock puts two updates on one index at once, and both
# write the same state and the same page rows; a full update on a large repo
# can legitimately outrun any ceiling worth setting, so age alone is not
# evidence of abandonment. A provably dead or recycled owner is a different
# matter and is still cleared immediately, which is the common crash case.
UPDATE_LOCK_STALE_AFTER_SECONDS = 30 * 60

# Past this age a live owner is reported as suspect rather than broken. It
# changes no behaviour, only what the user is told: "held for 9 hours" is the
# fact that makes a wedged update actionable, where a bare "still running"
# leaves them with nothing to act on.
UPDATE_LOCK_SUSPECT_AFTER_SECONDS = 30 * 60


def update_lock_path(repo_path: Path) -> Path:
    return Path(repo_path) / ".repowise" / UPDATE_LOCK_FILENAME


def workspace_update_lock_path(workspace_root: Path) -> Path:
    """Path of the workspace-level single-flight update lock.

    Unlike :func:`update_lock_path` (per-repo), this guards the whole
    workspace so a rebase's N post-commit hooks coalesce into one full
    ``update_workspace`` pass instead of N redundant ones. Lives under the
    workspace data dir (``.repowise-workspace/``) so it is shared by every
    member repo's update.
    """
    return Path(workspace_root) / ".repowise-workspace" / UPDATE_LOCK_FILENAME


def _try_acquire_lock_at(
    lock_path: Path,
    target_commit: str | None,
) -> dict[str, Any] | None:
    """Core exclusive-create acquire against an explicit lock path.

    Shared by the per-repo (:func:`try_acquire_update_lock`) and workspace
    (:func:`update_workspace_lock`) single-flight guards — one
    implementation for both, so the workspace guard inherits the same
    crash/liveness/coalescing semantics as the per-repo one.
    """
    from repowise.core.procutils import process_create_token

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

    def _read_existing() -> dict[str, Any] | None:
        return _read_lock_at(lock_path)

    for _ in range(2):
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(data, encoding="utf-8")
            # Atomic create-with-content: the lock either doesn't exist or
            # holds a complete payload — contenders can never read a
            # half-written file.
            os.link(tmp_path, lock_path)
        except FileExistsError:
            existing = _read_existing()
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
    # Both create attempts lost a race against a stale lock that was then
    # unlinked. Rather than falling through to a bare read — which can return
    # ``None`` ("acquired") with *no lock file on disk*, letting a caller
    # proceed without holding the lock — make one final exclusive create.
    # Winner: return ``None`` (owned). Loser: report the fresh winner.
    # Still-unreadable degrades to acquired, as everywhere.
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(data, encoding="utf-8")
        os.link(tmp_path, lock_path)
    except FileExistsError:
        return _read_existing()
    except OSError:
        return None
    finally:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
    return None


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
    return _try_acquire_lock_at(update_lock_path(repo_path), target_commit)


def update_workspace_lock(workspace_root: Path) -> dict[str, Any] | None:
    """Acquire the workspace-level single-flight guard. ``None`` means acquired.

    See :func:`try_acquire_update_lock` for the semantics; this is the same
    guard held against ``workspace_update_lock_path`` so two concurrent
    ``update_workspace`` runs coalesce instead of both re-indexing every
    member.
    """
    return _try_acquire_lock_at(workspace_update_lock_path(workspace_root), None)


def _release_lock_at(lock_path: Path) -> None:
    with contextlib.suppress(OSError):
        lock_path.unlink(missing_ok=True)


def release_update_lock(repo_path: Path) -> None:
    """Remove the per-repo update lock file. Safe to call if it doesn't exist."""
    _release_lock_at(update_lock_path(repo_path))


def release_workspace_lock(workspace_root: Path) -> None:
    """Remove the workspace-level update lock. Safe to call if it doesn't exist."""
    _release_lock_at(workspace_update_lock_path(workspace_root))


def _read_lock_at(lock_path: Path) -> dict[str, Any] | None:
    """Read a lock payload from an explicit path, applying liveness/staleness.

    Mirrors :func:`read_update_lock` against an arbitrary lock path so the
    workspace guard gets the same crash recovery: a dead or recycled owner's
    lock is treated as absent, a live owner's is honored regardless of age.
    """
    from repowise.core.procutils import pid_alive, process_create_token

    if not lock_path.exists():
        return None
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    age = lock_age_seconds(payload)
    if age is None:
        return None

    pid = payload.get("pid")
    if isinstance(pid, int) and pid > 0:
        alive = pid_alive(pid)
        if alive is False:
            return None
        if alive is True:
            stored_token = payload.get("pid_create_token")
            if isinstance(stored_token, str) and stored_token:
                current_token = process_create_token(pid)
                if current_token is not None and current_token != stored_token:
                    return None
            return payload

    # Liveness unknown: fall back to the wall clock.
    if age > UPDATE_LOCK_STALE_AFTER_SECONDS:
        return None
    return payload


def read_update_lock(repo_path: Path) -> dict[str, Any] | None:
    """Return the lock payload if present and not stale, else ``None``.

    A lock is stale when its owning PID is positively dead or has been
    recycled by an unrelated process. That probe is what stops a crashed or
    killed update (SIGKILL, power loss — paths atexit cannot cover) from
    blocking every later update.

    An owner we can positively see running is honoured no matter how old the
    lock is. The wall clock applies only when liveness cannot be established:
    a payload with no usable PID (written by an older version) or a probe that
    returned "unknown". Age on its own is not evidence that an update has
    stopped: a full update on a large repo can outrun any ceiling worth
    setting, and clearing the lock underneath it would put two updates on one
    index, both writing the same state and the same page rows. A live owner
    that has held the lock unreasonably long is surfaced to the user by the
    callers instead (see :func:`lock_is_suspect`), which is the reporting half
    of the same problem and cannot corrupt anything.
    """
    return _read_lock_at(update_lock_path(repo_path))


def lock_age_seconds(payload: dict[str, Any] | None) -> float | None:
    """Wall-clock age of a lock payload, or ``None`` when it cannot be told.

    One implementation because every reporting site needs the same number and
    each one deriving it separately is how the deferral message ended up
    quoting no age at all.
    """
    if not payload:
        return None
    started = payload.get("started_at")
    if not isinstance(started, (int, float)):
        return None
    return max(0.0, time.time() - started)


def format_lock_age(age: float | None) -> str:
    """Human phrasing for how long a lock has been held.

    Takes the seconds rather than the payload so a caller that already carries
    the age (a deferred repo result) does not have to rebuild a payload to ask.

    Coarsens with age on purpose: a lock held for seconds is normal and the
    seconds are the interesting part, while one held for hours is the whole
    point of the message and "32700s" buries it.
    """
    if age is None:
        return "for an unknown time"
    if age < 90:
        return f"for {int(age)}s"
    if age < 90 * 60:
        return f"for {int(age / 60)}m"
    return f"for {age / 3600:.1f}h"

