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
    "maybe_show_notice",
    "record",
    "record_command_run",
    "register_event",
    "registered_events",
    "status_lines",
]


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
