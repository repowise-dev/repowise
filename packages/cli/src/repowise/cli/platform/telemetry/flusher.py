"""Deliver spooled telemetry events, in a process nobody is waiting on.

Run as ``python -m repowise.cli.platform.telemetry.flusher``. The CLI spawns
this detached at exit and dies immediately; delivery then takes as long as the
network takes, instead of being raced by the command's own exit.

Deliberately narrow: it imports the spool and the platform client and nothing
else. In particular it must never import :mod:`.emitter`, whose ``atexit`` hook
would have this process spawn another one.
"""

from __future__ import annotations

import contextlib

#: Backend ingestion path (joined to the platform base URL by the client).
INGEST_PATH = "telemetry/events"


def deliver() -> int:
    """POST every queued envelope. Returns how many were sent. Never raises."""
    sent = 0
    with contextlib.suppress(Exception):
        from repowise.cli.platform.client import default_client
        from repowise.cli.platform.telemetry import spool

        for envelope in spool.claim():
            if default_client.post(INGEST_PATH, envelope):
                sent += 1
    return sent


if __name__ == "__main__":
    deliver()
