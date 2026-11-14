"""Build the wire envelope for an event and send it, best-effort.

Recording an event appends it to an on-disk spool and returns, so nothing on
the recording path talks to the network. On exit the process spawns a detached
flusher (:mod:`.flusher`) to deliver what is queued and dies without waiting
for it.

That indirection is the point. Sending used to happen on a daemon thread that
``atexit`` joined for up to 2s, which meant every short command paid the POST
(~750ms measured) after its output was already printed — the CLI waiting on the
network is exactly what the fire-and-forget thread was supposed to prevent, and
a thread cannot outlive the interpreter that owns it. A separate process can,
so delivery no longer competes with exit: the spawn costs a few milliseconds
and the network cost lands where nobody is waiting on it.

Delivery is still best-effort — a batch the backend refuses is dropped rather
than retried (see :mod:`.spool`) — so this trades none of the old design's
guarantees, only its latency.

Everything here is fail-silent: a telemetry failure must never surface to the
user.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import sys

from repowise.cli.platform import identity, settings
from repowise.cli.platform.telemetry import environment, spool
from repowise.cli.platform.telemetry.events import TelemetryEvent
from repowise.cli.spawn import spawn_detached

#: Module the detached flusher runs as.
_FLUSHER_MODULE = "repowise.cli.platform.telemetry.flusher"


def _cli_version() -> str:
    try:
        from repowise.cli import __version__

        return __version__
    except Exception:
        return "unknown"


def _under_test() -> bool:
    """Return whether a test runner is driving this process.

    The suite drives real CLI commands, and its fixtures relocate the home
    directory that holds ``anon_id``, so without this every test run would
    queue events under a freshly minted install. Only delivery is suppressed:
    consent resolution and the debug print stay real, so their tests still
    exercise them. Shared with the core emitter, which also honours the
    ``REPOWISE_TELEMETRY_IN_TESTS`` opt-in telemetry's own tests use.
    """
    from repowise.core.platform.telemetry import under_test

    return under_test()


def build_envelope(event: TelemetryEvent) -> dict[str, object]:
    """Assemble the anonymous wire envelope for *event*."""
    envelope: dict[str, object] = {
        "event": event.name,
        "anon_id": identity.get_anonymous_id(),
        "session_id": identity.get_session_id(),
        "cli_version": _cli_version(),
        "properties": {"client": environment.client(), **event.properties()},
    }
    envelope.update(environment.base_facts())
    return envelope


def record(event: TelemetryEvent) -> None:
    """Record *event*: respect consent, then debug-print or queue. Never raises."""
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
        if _under_test():
            return

        spool.append(envelope)
    except Exception:
        # Telemetry must never break a command.
        return


def _flusher_executable() -> str:
    """Return the executable for the detached flusher.

    On Windows (``os.name == "nt"``) prefer a ``pythonw.exe`` sibling next
    to ``sys.executable`` (windowless GUI subsystem, never allocates a
    console) when it exists as a file; otherwise fall back to
    ``sys.executable``.
    """
    if os.name == "nt" and sys.executable:
        candidate = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        with contextlib.suppress(Exception):
            if os.path.isfile(candidate):
                return candidate
    return sys.executable or ""


def _spawn_flusher() -> bool:
    """Start the detached delivery process. Returns whether it started.

    Fully detached (own session/console, no inherited streams) so it keeps
    running after this process exits and can never write to the user's
    terminal.
    """
    if not sys.executable:
        return False
    try:
        spawn_detached([_flusher_executable(), "-m", _FLUSHER_MODULE], os.getcwd())
    except Exception:
        return False
    return True


@atexit.register
def _flush() -> None:
    """Hand queued events to a detached flusher on the way out. Never raises."""
    with contextlib.suppress(Exception):
        if _under_test() or not spool.has_events():
            return
        if not settings.is_enabled() or settings.debug_mode():
            # Turning telemetry off must also unsend what is already queued,
            # or a disable would be followed by one last batch.
            spool.claim()
            return
        _spawn_flusher()
