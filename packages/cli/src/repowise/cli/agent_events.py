"""The NDJSON stream ``--progress json`` writes on stdout, for agents and scripts.

One JSON object per line. Every object carries ``v`` (:data:`SCHEMA_VERSION`)
and ``event``:

- ``stage``: a top-level stage opened. ``stage``, ``title``.
- ``phase``: a step started, finished or failed. ``phase`` (stable id),
  ``label``, ``state`` (``start`` / ``done`` / ``failed``), ``completed``,
  ``total`` (null when unknown), ``unit``, ``elapsed_s`` (null on ``start``).
- ``warning``: something the run skipped or degraded. ``level``, ``text``.
- ``summary``: always the last line. ``status`` is ``ready`` (exit 0),
  ``degraded`` (exit 2), ``interrupted`` (exit 130) or ``failed`` (any other
  exit, with ``error``); then ``mode``, ``restart_required``, ``spend``,
  ``warnings``, ``files_written`` and ``next`` (commands, first one first).

While the stream is open, human text goes to stderr, so stdout is only these lines.
"""

from __future__ import annotations

import contextlib
import json
import sys
from collections.abc import Iterator
from typing import IO, Any

from repowise.cli.exit_codes import EXIT_DEGRADED, EXIT_INTERRUPTED

#: Bumped on any change a consumer would have to handle: a renamed or removed
#: field, or a changed meaning. New fields do not bump it.
SCHEMA_VERSION = 1

STATUS_READY = "ready"
STATUS_DEGRADED = "degraded"
STATUS_INTERRUPTED = "interrupted"
STATUS_FAILED = "failed"

_STATUS_BY_EXIT = {0: STATUS_READY, EXIT_DEGRADED: STATUS_DEGRADED, EXIT_INTERRUPTED: STATUS_INTERRUPTED}


def status_for_exit(code: Any) -> str:
    """The summary status for a process exit code (``None`` is 0, as for ``SystemExit``)."""
    return _STATUS_BY_EXIT.get(0 if code is None else code, STATUS_FAILED)


def summary_fields(
    status: str,
    *,
    mode: dict[str, Any] | None = None,
    restart_required: bool = False,
    spend: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    files_written: list[str] | None = None,
    next_commands: list[str] | None = None,
    error: str | None = None,
    findings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Every summary key, always present, so a consumer never probes for one."""
    fields: dict[str, Any] = {
        "status": status,
        "mode": mode,
        "restart_required": restart_required,
        "spend": spend or {"tokens": 0, "usd": 0.0},
        "warnings": list(warnings or []),
        "files_written": list(files_written or []),
        "next": list(next_commands or []),
        # What the finish panel's "What stood out" showed; null before it rendered.
        "findings": findings,
    }
    if error is not None:
        fields["error"] = error
    return fields


class EventStream:
    """Writes one event per line and flushes, so a reader sees each as it happens."""

    def __init__(self, stream: IO[str]) -> None:
        self._stream = stream
        self.summary_sent = False

    def emit(self, event: str, **fields: Any) -> None:
        line = json.dumps({"v": SCHEMA_VERSION, "event": event, **fields}, default=str)
        self._stream.write(line + "\n")
        self._stream.flush()

    def summary(self, fields: dict[str, Any]) -> None:
        """The last line. Only the first call writes: the most specific caller wins."""
        if self.summary_sent:
            return
        self.summary_sent = True
        self.emit("summary", **fields)


_active: EventStream | None = None


def active() -> EventStream | None:
    """The open stream, or None when output is for a person."""
    return _active


@contextlib.contextmanager
def open_stream(console: Any) -> Iterator[EventStream]:
    """Point stdout at the event stream and *console* at stderr until the block ends."""
    from repowise.cli.helpers import silence_logs_for_machine_output
    from repowise.cli.output import line_output_width, resolve_console_width

    global _active
    # ``_file`` is None for a console that follows sys.stdout, and ``_width``
    # None for one that measures its terminal; restore exactly those.
    previous_file, previous_width = console._file, console._width
    stream = EventStream(sys.stdout)
    console.file = sys.stderr
    console._width = resolve_console_width(sys.stderr)
    _active = stream
    try:
        with silence_logs_for_machine_output(), line_output_width(console, sys.stderr):
            yield stream
    finally:
        _active = None
        console.file = previous_file
        console._width = previous_width
