"""Engine event response models: CloudEvents 1.0.2 structured JSON.

``data`` mirrors ``repowise.core.events.model.to_cloudevent``: OTel ``vcs.*``
names for the revisions, ``repowise.*`` for kind, component, severity and
stamp, and ``evidence`` with the figures that moved.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class CloudEvent(BaseModel):
    specversion: str
    id: str
    source: str
    type: str
    time: str
    datacontenttype: str
    #: ``component:<name>``; absent for a repository-wide event.
    subject: str | None = None
    data: dict[str, Any]


class EventListResponse(BaseModel):
    events: list[CloudEvent]
    #: Component snapshots stored; events need two consecutive ones.
    snapshot_runs: int = 0
    #: Why ``events`` is empty (``repowise.core.events.store.empty_events_reason``);
    #: ``None`` when it is not.
    empty_reason: Literal["baseline_only", "no_match", "nothing_moved"] | None = None
    #: The same reason as a sentence, as ``repowise events`` prints it.
    empty_note: str | None = None
