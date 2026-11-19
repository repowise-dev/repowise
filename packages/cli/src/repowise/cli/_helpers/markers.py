"""Update queued/pending markers and the capped hook output log."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from repowise.cli._helpers.git import as_commit_id
from repowise.cli._helpers.paths import ensure_repowise_dir, get_repowise_dir

# ---------------------------------------------------------------------------
# Queued / pending markers — coordinate the post-commit hook with a running
# update so rapid-fire commits don't spawn N concurrent updates that race
# on save_state. Two distinct markers, deliberately:
#
#   ``.update.queued``  : written by the hook BEFORE backgrounding repowise
#                         update. Closes the race window between commit and
#                         lock acquisition — the augment hook reads this
#                         and suppresses its warning the moment the queued
#                         file appears, not 30+ seconds later when the
#                         actual lock file lands on disk.
#
#   ``.update.pending`` : written by a *new* update_cmd invocation when it
#                         finds an in-flight lock. Carries the latest HEAD
#                         so the running update can roll forward to it at
#                         the end of its current pass instead of stopping
#                         at a stale commit.
#
# Both markers are best-effort: failure to write/read them must never break
# update_cmd itself, only degrade the coalescing behaviour to "spawn but
# bail" (slightly noisier in the augment hook but still correct).
# ---------------------------------------------------------------------------

UPDATE_QUEUED_FILENAME = ".update.queued"
UPDATE_PENDING_FILENAME = ".update.pending"

# A ``.update.queued`` marker older than this is treated as stale — most
# likely a crashed hook that wrote the marker but never spawned the update.
# Short enough to avoid suppressing genuinely-stale warnings indefinitely.
UPDATE_QUEUED_STALE_AFTER_SECONDS = 5 * 60


def _update_queued_path(repo_path: Path) -> Path:
    return get_repowise_dir(repo_path) / UPDATE_QUEUED_FILENAME


def _update_pending_path(repo_path: Path) -> Path:
    return get_repowise_dir(repo_path) / UPDATE_PENDING_FILENAME


def write_update_queued(repo_path: Path, head: str | None) -> None:
    """Mark that an update has been spawned for ``head``.

    Called from the post-commit hook *before* backgrounding ``repowise
    update`` so the augment hook can suppress its stale-wiki warning during
    the brief window where the actual update process is still starting up
    (Python import, DB open, etc.) and hasn't yet written its own lock file.
    """
    import time

    try:
        ensure_repowise_dir(repo_path)
    except OSError:
        return
    payload = {"target_commit": head, "queued_at": time.time()}
    with contextlib.suppress(OSError):
        _update_queued_path(repo_path).write_text(json.dumps(payload), encoding="utf-8")


def read_update_queued(repo_path: Path) -> dict[str, Any] | None:
    """Return queued payload if fresh (≤ ``UPDATE_QUEUED_STALE_AFTER_SECONDS``)."""
    import time

    path = _update_queued_path(repo_path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    queued_at = payload.get("queued_at")
    if not isinstance(queued_at, (int, float)):
        return None
    if time.time() - queued_at > UPDATE_QUEUED_STALE_AFTER_SECONDS:
        return None
    return payload


def clear_update_queued(repo_path: Path) -> None:
    """Drop the queued marker. Called by update_cmd once it owns the real lock."""
    with contextlib.suppress(OSError):
        _update_queued_path(repo_path).unlink(missing_ok=True)


def write_update_pending(repo_path: Path, head: str | None) -> None:
    """Record that another commit landed while an update was in flight.

    The running update reads this at the end of its pass and rolls forward
    to the new HEAD in one extra round, avoiding the failure mode where a
    rapid burst of commits leaves the wiki indexed to an outdated commit.
    """
    if head is None:
        return
    try:
        ensure_repowise_dir(repo_path)
    except OSError:
        return
    with contextlib.suppress(OSError):
        _update_pending_path(repo_path).write_text(head, encoding="utf-8")


def read_update_pending(repo_path: Path) -> str | None:
    """Return the pending HEAD if any, else None."""
    path = _update_pending_path(repo_path)
    if not path.exists():
        return None
    try:
        head = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return head or None


def clear_update_pending(repo_path: Path) -> None:
    """Drop the pending marker once the rolled-forward update has consumed it."""
    with contextlib.suppress(OSError):
        _update_pending_path(repo_path).unlink(missing_ok=True)


def _pending_commit_still_ahead(
    repo_path: Path, pending_head: str, indexed_head: str | None
) -> bool:
    """True only when ``pending_head`` is a resolvable commit strictly ahead of
    ``indexed_head`` — a newer commit the index has not caught up to yet.

    Equal commits, ancestors of ``indexed_head``, and commits that no longer
    resolve (rebased or gc'd away) all return ``False``, so the caller clears
    them instead of leaving the marker behind forever.
    """
    if not indexed_head or pending_head == indexed_head:
        return False
    import subprocess

    if as_commit_id(indexed_head) is None or as_commit_id(pending_head) is None:
        return False
    try:
        # ``indexed_head`` is an ancestor of ``pending_head`` => pending is
        # newer than what we indexed and worth keeping. A non-zero exit
        # (including an unresolvable pending commit) means "not ahead".
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", "--end-of-options", indexed_head, pending_head],
            cwd=str(repo_path),
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def consume_update_pending(repo_path: Path, indexed_head: str | None) -> None:
    """Clear the ``.update.pending`` marker once the index has caught up to it.

    A bailed update writes the latest HEAD to ``.update.pending`` (see
    :func:`write_update_pending`) so the in-flight update can tell more commits
    landed. The update that actually holds the lock calls this when it
    finishes: the marker is obsolete unless it points to a commit strictly
    *ahead* of the one just indexed. The previous consumer only cleared on an
    exact ``pending == head`` match, so once HEAD advanced past the pending
    commit (or that commit was rebased away) the marker leaked indefinitely.
    """
    pending_head = read_update_pending(repo_path)
    if pending_head is None:
        return
    if not _pending_commit_still_ahead(repo_path, pending_head, indexed_head):
        clear_update_pending(repo_path)


# ---------------------------------------------------------------------------
# Hook output log — capped, single-file rotation so the user can diagnose
# why the post-commit hook didn't catch up without needing to chase down a
# silent subprocess. Cap is deliberately small: a few recent runs is enough
# context, and we don't want a runaway log to fill the .repowise/ dir.
# ---------------------------------------------------------------------------

UPDATE_LOG_FILENAME = ".update.log"

# Truncate the log when it grows past this size, keeping the tail.
UPDATE_LOG_MAX_BYTES = 256 * 1024
# After truncation, retain at most this much of the prior tail.
UPDATE_LOG_KEEP_TAIL_BYTES = 64 * 1024


def update_log_path(repo_path: Path) -> Path:
    return get_repowise_dir(repo_path) / UPDATE_LOG_FILENAME


def rotate_update_log_if_needed(repo_path: Path) -> None:
    """Truncate ``.update.log`` if it has grown past the size cap.

    Called opportunistically from the hook before piping a new run's output
    in. We use simple in-place truncation (rewrite the tail) rather than
    renaming, because the post-commit hook can fire in parallel with a
    `repowise update` that may still be writing — a rename would orphan
    the writer's file descriptor on POSIX and outright fail on Windows.
    """
    path = update_log_path(repo_path)
    try:
        if not path.exists() or path.stat().st_size <= UPDATE_LOG_MAX_BYTES:
            return
        with path.open("rb") as f:
            f.seek(-UPDATE_LOG_KEEP_TAIL_BYTES, 2)
            tail = f.read()
        path.write_bytes(b"... (log truncated) ...\n" + tail)
    except OSError:
        pass
