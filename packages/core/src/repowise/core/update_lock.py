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
    # Both create attempts lost a race against a stale lock that was then
    # unlinked. Rather than falling through to a bare ``read_update_lock``
    # — which can return ``None`` ("acquired") with *no lock file on disk*,
    # letting a caller proceed without holding the lock — make one final
    # exclusive create. Winner: return ``None`` (owned). Loser: report the
    # fresh winner. Still-unreadable degrades to acquired, as everywhere.
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(data, encoding="utf-8")
        os.link(tmp_path, lock_path)
    except FileExistsError:
        return read_update_lock(repo_path)
    except OSError:
        return None
    finally:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
    return None


def release_update_lock(repo_path: Path) -> None:
    """Remove the update lock file. Safe to call if it doesn't exist."""
    with contextlib.suppress(OSError):
        update_lock_path(repo_path).unlink(missing_ok=True)


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
    from repowise.core.procutils import pid_alive, process_create_token

    lock_path = update_lock_path(repo_path)
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
            # Legacy locks (pre-token) skip the identity check: liveness is
            # all we have, and it is still better evidence than the clock.
            if isinstance(stored_token, str) and stored_token:
                current_token = process_create_token(pid)
                if current_token is not None and current_token != stored_token:
                    return None
            return payload

    # Liveness unknown: fall back to the wall clock.
    if age > UPDATE_LOCK_STALE_AFTER_SECONDS:
        return None
    return payload
