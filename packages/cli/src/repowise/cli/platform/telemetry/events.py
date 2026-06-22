"""Telemetry event types and the registry that makes them extendable.

A telemetry event is a small, declarative object: a stable ``name`` plus a
``properties()`` dict of anonymous, non-identifying fields. To collect a new
kind of data later, add a subclass and decorate it with :func:`register_event`
— the emitter serializes any :class:`TelemetryEvent` uniformly, so no other
code changes.

Privacy contract for every event (enforced by convention + code review): never
put source code, file paths, repo/symbol names, flag *values*, env-var values,
error messages, stack traces, or anything user-identifiable into
``properties()``. Flag *names* and class names of exceptions are allowed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

#: name -> event class. Lets the (future) server validate names and lets new
#: event types self-register without edits elsewhere.
_REGISTRY: dict[str, type[TelemetryEvent]] = {}


def register_event(cls: type[TelemetryEvent]) -> type[TelemetryEvent]:
    """Class decorator: register an event type under its ``name``."""
    _REGISTRY[cls.name] = cls
    return cls


def registered_events() -> dict[str, type[TelemetryEvent]]:
    """Return a copy of the event registry (name -> class)."""
    return dict(_REGISTRY)


class TelemetryEvent:
    """Base class for all telemetry events.

    Subclasses set the class-level :attr:`name` and implement
    :meth:`properties`. Keep ``properties`` anonymous (see module docstring).
    """

    name: ClassVar[str] = "event"

    def properties(self) -> dict[str, Any]:
        raise NotImplementedError


@register_event
@dataclass
class CommandRunEvent(TelemetryEvent):
    """Emitted once per CLI invocation from the central command wrapper.

    Captures *what* ran and *how it went* — never *on what*. ``flags`` holds
    option names only (``--resume``), never their values.
    """

    name: ClassVar[str] = "command_run"

    command: str
    subcommand: str | None = None
    flags: list[str] = field(default_factory=list)
    status: str = "ok"  # "ok" | "error"
    error_type: str | None = None  # exception class name only, no message
    duration_ms: int = 0
    # Open extension point for command-specific anonymous fields (bucketed
    # counts, coarse enums). Callers must keep these non-identifying.
    extra: dict[str, Any] = field(default_factory=dict)

    def properties(self) -> dict[str, Any]:
        props: dict[str, Any] = {
            "command": self.command,
            "subcommand": self.subcommand,
            "flags": self.flags,
            "status": self.status,
            "error_type": self.error_type,
            "duration_ms": self.duration_ms,
        }
        if self.extra:
            props.update(self.extra)
        return props
