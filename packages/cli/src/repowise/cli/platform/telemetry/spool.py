"""On-disk queue for telemetry envelopes (``~/.repowise/telemetry-spool.jsonl``).

A CLI process records its one ``command_run`` event in the last moments before
exit, so there is no time left to deliver it without making the user wait. The
spool decouples the two: recording is a file append, and delivery is the job of
the detached flusher the process spawns on its way out.

One JSON envelope per line. A flusher claims the whole file with a single
atomic rename, so several of them running at once (the agent's shell calls are
routinely concurrent) can never send the same event twice.

A claim is destructive: a batch whose POST fails is dropped, not retried, which
matches what the previous fire-and-forget send did with anything the backend
did not accept. Turning this into a real outbox (requeue with a per-event retry
count) is the upgrade path if offline delivery ever matters; it is not free,
because a POST that times out after the server accepted it would then arrive
twice.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

SPOOL_FILENAME = "telemetry-spool.jsonl"

#: Discard the spool outright past this size. Only reachable if delivery has
#: failed for a very long time (offline machine), where old events are worthless.
_MAX_BYTES = 1 << 20

#: Most events delivered from one claim; a backlog past this is dropped oldest-first.
_MAX_BATCH = 100


def _path() -> Path:
    from repowise.cli.helpers import user_global_dir

    return user_global_dir() / SPOOL_FILENAME


def append(envelope: dict[str, object]) -> None:
    """Queue *envelope* for later delivery. Best-effort, never raises."""
    with contextlib.suppress(Exception):
        path = _path()
        with contextlib.suppress(OSError):
            if path.stat().st_size > _MAX_BYTES:
                path.unlink()
        line = json.dumps(envelope, separators=(",", ":"))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def has_events() -> bool:
    """Return whether anything is queued, without reading the file."""
    try:
        return _path().stat().st_size > 0
    except OSError:
        return False


def claim() -> list[dict[str, object]]:
    """Take exclusive ownership of everything queued and return it.

    The rename is the mutual exclusion: whichever process renames the spool
    first owns those events, and every other process sees an empty queue.
    Events queued after the rename land in a fresh spool file for the next
    claim, so nothing is stranded.
    """
    path = _path()
    claimed = path.with_name(f"{path.name}.{os.getpid()}.claim")
    try:
        os.replace(path, claimed)
    except OSError:
        # Nothing queued, or another process claimed it first.
        return []

    text = ""
    with contextlib.suppress(OSError):
        text = claimed.read_text(encoding="utf-8")
    with contextlib.suppress(OSError):
        claimed.unlink()

    events: list[dict[str, object]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        with contextlib.suppress(Exception):
            envelope = json.loads(line)
            if isinstance(envelope, dict):
                events.append(envelope)
    return events[-_MAX_BATCH:]
