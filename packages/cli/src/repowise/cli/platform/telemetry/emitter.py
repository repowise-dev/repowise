"""Build the wire envelope for an event and send it, best-effort.

Sending is fire-and-forget on a daemon thread so a slow or unreachable backend
never delays a CLI command. An ``atexit`` flush briefly joins in-flight sends
so short-lived invocations still deliver. Everything here is fail-silent: a
telemetry failure must never surface to the user.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import sys
import threading

from repowise.cli.platform import identity, settings
from repowise.cli.platform.client import default_client
from repowise.cli.platform.telemetry import environment
from repowise.cli.platform.telemetry.events import TelemetryEvent

#: Backend ingestion path (joined to the platform base URL by the client).
_INGEST_PATH = "telemetry/events"

#: In-flight send threads, joined briefly at exit so quick commands deliver.
_pending: list[threading.Thread] = []


def _cli_version() -> str:
    try:
        from repowise.cli import __version__

        return __version__
    except Exception:
        return "unknown"


def build_envelope(event: TelemetryEvent) -> dict[str, object]:
    """Assemble the anonymous wire envelope for *event*."""
    envelope: dict[str, object] = {
        "event": event.name,
        "anon_id": identity.get_anonymous_id(),
        "session_id": identity.get_session_id(),
        "cli_version": _cli_version(),
        "properties": event.properties(),
    }
    envelope.update(environment.base_facts())
    return envelope


def record(event: TelemetryEvent) -> None:
    """Record *event*: respect consent, then debug-print or send. Never raises."""
    try:
        if not settings.is_enabled():
            return
        envelope = build_envelope(event)

        if settings.debug_mode():
            # Verifiability: show exactly what would be sent, send nothing.
            print(
                "[repowise telemetry] would send:\n" + json.dumps(envelope, indent=2),
                file=sys.stderr,
            )
            return

        thread = threading.Thread(
            target=default_client.post,
            args=(_INGEST_PATH, envelope),
            daemon=True,
        )
        thread.start()
        # Prune finished sends so a long-lived process (e.g. ``serve``/``watch``)
        # emitting many events never accumulates dead Thread objects.
        _pending[:] = [t for t in _pending if t.is_alive()]
        _pending.append(thread)
    except Exception:
        # Telemetry must never break a command.
        return


@atexit.register
def _flush() -> None:
    """Give in-flight sends a brief moment to complete on process exit.

    Bounded to ~2s total across all pending sends so exit is never noticeably
    delayed even if the backend is hung.
    """
    import time

    remaining = 2.0
    for thread in _pending:
        if remaining <= 0:
            break
        start = time.monotonic()
        with contextlib.suppress(Exception):
            thread.join(timeout=remaining)
        remaining -= time.monotonic() - start
