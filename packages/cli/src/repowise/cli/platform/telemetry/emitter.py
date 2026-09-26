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
import subprocess
import sys

from repowise.cli.platform import identity, settings
from repowise.cli.platform.telemetry import environment, spool
from repowise.cli.platform.telemetry.events import TelemetryEvent

#: Module the detached flusher runs as.
_FLUSHER_MODULE = "repowise.cli.platform.telemetry.flusher"


def _cli_version() -> str:
    try:
        from repowise.cli import __version__

        return __version__
    except Exception:
        return "unknown"


def _under_test() -> bool:
    """Return whether we are running inside a pytest session.

    The suite drives real CLI commands, and its fixtures relocate the home
    directory that holds ``anon_id``, so without this every test run delivers
    events under a freshly minted install. Only delivery is suppressed:
    consent resolution stays real, so its own tests still exercise the actual
    precedence rules.

    Shares the env-var check with :func:`environment.under_pytest` so the two
    cannot drift. The ``sys.modules`` fallback is kept because this guard is
    only consulted in-process, where an import is evidence enough.
    """
    return environment.under_pytest() or "pytest" in sys.modules


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
    executable = _flusher_executable()
    if not executable:
        return False
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "cwd": os.getcwd(),
    }
    if os.name == "nt":
        # CREATE_NO_WINDOW alone: no console window flashes up
        # in front of the user between commands. Do NOT add DETACHED_PROCESS
        # here — Microsoft documents that CREATE_NO_WINDOW is ignored when
        # combined with DETACHED_PROCESS, which leaves a console app with no
        # console to inherit and Windows gives it a new visible one (the flash).
        # A Windows child outlives its parent without DETACHED_PROCESS.
        kwargs["creationflags"] = 0x08000000
        try:
            if hasattr(subprocess, "STARTUPINFO"):
                startupinfo = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
                startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
                startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)  # type: ignore[attr-defined]
                kwargs["startupinfo"] = startupinfo
        except Exception:
            pass
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([executable, "-m", _FLUSHER_MODULE], **kwargs)  # type: ignore[arg-type]
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
