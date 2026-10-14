"""Where events go after they are stored: the ``event-sink`` seam.

OSS ships two local sinks and no network delivery: ``jsonl`` appends each
event as one CloudEvents JSON line to ``.repowise/events.jsonl`` (the
default), and ``stdout`` prints the same lines for CI. A plugin registers
another sink (a webhook, a chat app, a SIEM) under ``SeamRegistry("event-sink")``
and a repo names the sinks it wants under ``events.sinks`` in
``.repowise/config.yaml``. Event ids are stable, so a consumer dedupes a
re-delivered event by ``id``; a webhook sink signs with Standard Webhooks.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from repowise.core.registry.plugins import SeamRegistry

from .model import EngineEvent, to_cloudevent

EVENTS_FILE = "events.jsonl"


class EventSink(Protocol):
    async def deliver(self, events: Sequence[EngineEvent], *, repo_root: Path) -> None:
        """Deliver *events*, at least once; raising leaves them in the store only."""
        ...


def _lines(events: Sequence[EngineEvent]) -> str:
    return "".join(json.dumps(to_cloudevent(e), sort_keys=True) + "\n" for e in events)


class JsonlSink:
    """Appends to one local file. Ceiling: the file is never rotated or trimmed
    (retention applies to the store only); a consumer that needs a bounded file
    truncates it after reading, or reads the pull API instead."""

    async def deliver(self, events: Sequence[EngineEvent], *, repo_root: Path) -> None:
        path = Path(repo_root) / ".repowise" / EVENTS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(_lines(events))


class StdoutSink:
    async def deliver(self, events: Sequence[EngineEvent], *, repo_root: Path) -> None:
        sys.stdout.write(_lines(events))
        sys.stdout.flush()


event_sinks: SeamRegistry[EventSink] = SeamRegistry("event-sink")
event_sinks.register("jsonl", JsonlSink(), default=True)
event_sinks.register("stdout", StdoutSink())


__all__ = ["EVENTS_FILE", "EventSink", "JsonlSink", "StdoutSink", "event_sinks"]
