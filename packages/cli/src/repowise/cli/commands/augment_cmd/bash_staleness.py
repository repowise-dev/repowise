"""PostToolUse Bash: stale-wiki detection after git commits."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from ._shared import _find_repo_root

_GIT_COMMIT_PATTERNS = (
    "git commit",
    "git merge",
    "git rebase",
    "git cherry-pick",
    "git pull",
)


def _handle_bash_post(tool_input: dict, tool_output: object, cwd: str) -> str | None:
    """After a successful git commit, check if the wiki needs updating.

    Three-state response to "state.json is behind HEAD":

      1. A real ``.update.lock`` is held → an update is actively running.
         Emit a *positive* notice ("updating in background") so the agent
         knows the system is healing itself. Squelches the noisy stale
         warning during the long tail of large updates.

      2. A fresh ``.update.queued`` marker exists (post-commit hook just
         spawned a new update but the lock file isn't on disk yet) → also
         emit the positive notice. Closes the race window between commit
         and update start where we'd otherwise warn for ~5s.

      3. Neither marker → the update didn't run or already finished. Warn
         once per HEAD as before, so the user knows the wiki is genuinely
         out of sync.
    """
    output = tool_output if isinstance(tool_output, dict) else {"stdout": str(tool_output)}
    exit_code = _extract_exit_code(output)
    if exit_code is None:
        stdout = output.get("stdout", "")
        stderr = output.get("stderr", "")
        combined = f"{stdout}\n{stderr}".lower()
        if "error" in combined or "fatal" in combined:
            return None
    elif exit_code != 0:
        return None

    cmd = tool_input.get("command", "")
    if not isinstance(cmd, str) or not any(p in cmd for p in _GIT_COMMIT_PATTERNS):
        return None

    repo_path = _find_repo_root(Path(cwd))
    if repo_path is None:
        return None

    state_path = repo_path / ".repowise" / "state.json"
    if not state_path.exists():
        return None

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    last_sync = state.get("last_sync_commit")
    if not last_sync:
        return None

    try:
        import subprocess

        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except Exception:
        return None

    if head == last_sync:
        return None

    # Active update? Tell the agent the system is healing itself instead of
    # repeating the stale warning every commit. Per-head de-dup applies
    # equally to the positive notice so the chat doesn't get a notice spam
    # for the same HEAD over multiple tool calls.
    in_flight = _read_in_flight_marker(repo_path)
    if in_flight is not None:
        if _already_warned(repo_path, head):
            return None
        _record_warning(repo_path, head)
        target_short = (in_flight.get("target_commit") or head)[:8]
        elapsed = in_flight.get("elapsed_seconds")
        elapsed_str = (
            f"started {int(elapsed)}s ago" if isinstance(elapsed, (int, float)) else "running now"
        )
        return (
            f"[repowise] Wiki update in background — {elapsed_str}, "
            f"target {target_short}. State will catch up once it finishes."
        )

    if _already_warned(repo_path, head):
        return None
    _record_warning(repo_path, head)

    docs_enabled = state.get("docs_enabled", True)
    artifact = "Wiki" if docs_enabled else "Index"
    return (
        f"[repowise] {artifact} is stale — last indexed at commit "
        f"{last_sync[:8]}, HEAD is now {head[:8]}. "
        "Run `repowise update` to refresh documentation and graph context."
    )


def _read_in_flight_marker(repo_path: object) -> dict | None:
    """Return a normalised in-flight marker, or None when nothing is running.

    Considers two on-disk signals as evidence of an in-flight update:

      * ``.update.lock``   — written by ``update_cmd`` once it starts the
        actual work. Authoritative.
      * ``.update.queued`` — written by the post-commit hook *before*
        backgrounding the update, to close the start-up race window.

    Both have a freshness window so an aborted run can't suppress real
    warnings indefinitely.
    """
    import time
    from pathlib import Path

    repo_path = Path(repo_path)
    now = time.time()

    # Delegate lock freshness to the canonical reader: it layers a live-PID
    # probe on top of the wall-clock window, so a crashed update's leftover
    # lock can't suppress real stale-wiki warnings until the window expires.
    from repowise.cli.helpers import read_update_lock

    try:
        payload = read_update_lock(repo_path)
    except Exception:
        payload = None
    if payload is not None:
        started = payload.get("started_at")
        if isinstance(started, (int, float)):
            return {
                "source": "lock",
                "target_commit": payload.get("target_commit"),
                "elapsed_seconds": now - started,
            }

    queued_path = repo_path / ".repowise" / ".update.queued"
    if queued_path.exists():
        try:
            payload = json.loads(queued_path.read_text(encoding="utf-8"))
            queued_at = payload.get("queued_at")
            if isinstance(queued_at, (int, float)) and now - queued_at <= 5 * 60:
                return {
                    "source": "queued",
                    "target_commit": payload.get("target_commit"),
                    "elapsed_seconds": now - queued_at,
                }
        except (json.JSONDecodeError, OSError):
            pass

    return None


def _already_warned(repo_path: object, head: str) -> bool:
    from pathlib import Path

    marker = Path(repo_path) / ".repowise" / ".augment-warned"
    if not marker.exists():
        return False
    try:
        return marker.read_text(encoding="utf-8").strip() == head
    except OSError:
        return False


def _record_warning(repo_path: object, head: str) -> None:
    from pathlib import Path

    marker = Path(repo_path) / ".repowise" / ".augment-warned"
    with contextlib.suppress(OSError):
        marker.write_text(head, encoding="utf-8")


def _extract_exit_code(tool_output: dict) -> int | None:
    """Extract a process exit code from known hook output shapes."""
    for key in ("exit_code", "exitCode", "status"):
        value = tool_output.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None
