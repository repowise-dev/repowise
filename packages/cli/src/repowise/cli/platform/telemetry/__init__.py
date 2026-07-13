"""Anonymous, opt-out CLI telemetry.

Public surface used by the rest of the CLI:

* :func:`record_command_run` — central per-invocation event (called once from
  the root command wrapper in ``main.py``).
* :func:`maybe_show_notice` — the one-time opt-out notice.
* :func:`record` — emit any :class:`TelemetryEvent` (extension point for
  future, richer events).

Everything is fail-silent and respects consent; see :mod:`..settings`.
"""

from __future__ import annotations

from typing import Any

from repowise.cli.platform.telemetry.consent import (
    TELEMETRY_DOC_URL,
    maybe_show_notice,
    status_lines,
)
from repowise.cli.platform.telemetry.emitter import record
from repowise.cli.platform.telemetry.events import (
    CommandRunEvent,
    TelemetryEvent,
    register_event,
    registered_events,
)

__all__ = [
    "TELEMETRY_DOC_URL",
    "CommandRunEvent",
    "TelemetryEvent",
    "add_command_outcome",
    "drain_command_outcome",
    "maybe_show_notice",
    "record",
    "record_command_run",
    "register_event",
    "registered_events",
    "status_lines",
]


#: Anonymous outcome fields a command stashes for THIS invocation's
#: ``command_run`` event. A CLI process runs exactly one command, so a plain
#: module global is sufficient (no contextvar needed); the root wrapper drains
#: it once when it records the event.
_command_outcome: dict[str, Any] = {}


def bucket_count(n: int) -> str:
    """Bucket a count into a coarse, non-identifying range for telemetry.

    Exact file/page counts can help fingerprint a specific repo; buckets give
    the size distribution we actually want without that risk.
    """
    if n <= 0:
        return "0"
    if n < 10:
        return "1-9"
    if n < 100:
        return "10-99"
    if n < 500:
        return "100-499"
    if n < 1000:
        return "500-999"
    if n < 5000:
        return "1k-5k"
    return "5k+"


def add_command_outcome(**fields: Any) -> None:
    """Attach anonymous outcome fields to this invocation's ``command_run`` event.

    For coarse, non-identifying context a command wants reported alongside the
    generic outcome: bucketed counts, coarse enums, booleans (e.g. an index's
    file-count bucket or configured provider). Same privacy contract as events —
    never pass source, paths, repo/symbol names, or raw values.
    """
    _command_outcome.update(fields)


def drain_command_outcome() -> dict[str, Any]:
    """Return and clear the stashed outcome fields (called once by the wrapper)."""
    outcome = dict(_command_outcome)
    _command_outcome.clear()
    return outcome


def record_command_run(
    command: str,
    *,
    subcommand: str | None = None,
    flags: list[str] | None = None,
    status: str = "ok",
    error_type: str | None = None,
    duration_ms: int = 0,
    extra: dict[str, Any] | None = None,
) -> None:
    """Record a :class:`CommandRunEvent`. Convenience wrapper over :func:`record`."""
    record(
        CommandRunEvent(
            command=command,
            subcommand=subcommand,
            flags=list(flags or []),
            status=status,
            error_type=error_type,
            duration_ms=duration_ms,
            extra=dict(extra or {}),
        )
    )
