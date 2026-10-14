"""Engine event response models: CloudEvents 1.0.2 structured JSON.

``data`` mirrors ``repowise.core.events.model.to_cloudevent``: OTel ``vcs.*``
names for the revisions, ``repowise.*`` for kind, component, severity and
stamp, and ``evidence`` with the figures that moved.
"""

from __future__ import annotations

from typing import Any

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
