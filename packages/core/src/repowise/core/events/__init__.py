"""Component snapshots per index run, and typed events diffed from them.

Each index run stores component-level facts (:mod:`.snapshot`) under the
analyzer and parser stamp it ran with. Two consecutive runs with the same
stamp diff into typed occurrences (:mod:`.diff`), stored as events
(:mod:`.store`) and handed to the ``event-sink`` seam (:mod:`.sinks`) in
their CloudEvents form (:mod:`.model`).
"""

from __future__ import annotations

from repowise.core.events.model import KINDS, EngineEvent, EventKind, to_cloudevent

__all__ = ["KINDS", "EngineEvent", "EventKind", "to_cloudevent"]
