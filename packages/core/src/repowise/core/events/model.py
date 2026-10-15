"""The internal event shape, the kinds, and their CloudEvents wire form.

An :class:`EngineEvent` is what the engine stores and hands to sinks. On the
wire it becomes a CloudEvents 1.0.2 structured JSON event: ``type`` is a
CDEvents type where CDEvents defines one for the occurrence, else a
``dev.repowise.<subject>.<predicate>.<version>`` string in the same versioned
shape; ``data`` carries OTel ``vcs.*`` attribute names for the revision facts.
No kind here has a CDEvents counterpart (CDEvents v0.5.1 covers repository,
branch, change, build, test, deploy, incident and ticket occurrences).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

#: Bumped when an event's ``evidence`` shape changes meaning.
EVENT_SCHEMA_VERSION = 1
CLOUDEVENTS_SPECVERSION = "1.0"


@dataclass(frozen=True)
class EventKind:
    name: str
    type: str
    #: Off by default when the replay found it noisy; config turns it on.
    default_on: bool
    summary: str


def _kind(name: str, subject: str, predicate: str, on: bool, summary: str) -> EventKind:
    return EventKind(name, f"dev.repowise.{subject}.{predicate}.0.1.0", on, summary)


KINDS: dict[str, EventKind] = {
    k.name: k
    for k in (
        _kind("ownership_shift", "ownership", "shifted", True, "declared owner changed"),
        _kind("owner_inactive", "owner", "inactive", True, "main owners stopped changing it"),
        _kind("bus_factor_drop", "busfactor", "dropped", True, "bus factor fell"),
        _kind("new_hotspot", "hotspot", "added", False, "files became hotspots"),
        _kind("contract_added", "contract", "added", True, "a provided contract appeared"),
        _kind("contract_broken", "contract", "broken", True, "a contract with consumers disappeared"),
        _kind("decision_stale", "decision", "stale", True, "a governing decision went stale"),
        _kind("dead_code_growth", "deadcode", "grew", True, "dead-code findings grew"),
        _kind("test_reach_drop", "testreach", "dropped", True, "test reach fell"),
    )
}

#: Component criticality to event severity; a component with none is ``info``.
SEVERITY_BY_CRITICALITY = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
}


def severity_for(criticality: str | None) -> str:
    return SEVERITY_BY_CRITICALITY.get(criticality or "", "info")


@dataclass(frozen=True)
class EngineEvent:
    id: str
    kind: str
    repo_id: str
    component: str | None
    severity: str
    occurred_at: datetime
    evidence: Mapping[str, Any]
    stamp: str
    commit_sha: str | None = None
    base_commit_sha: str | None = None
    #: CloudEvents ``source``: the repository URL when known, else its path.
    source: str = ""
    #: Revision facts as OTel ``vcs.*`` names (``vcs.change.id`` and such).
    vcs: Mapping[str, Any] = field(default_factory=dict)
    schema: int = EVENT_SCHEMA_VERSION


def event_id(*parts: str | None) -> str:
    """A stable id for one occurrence, so re-delivery stays idempotent."""
    raw = "\x1f".join(p or "" for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def to_cloudevent(event: EngineEvent) -> dict[str, Any]:
    """The CloudEvents 1.0.2 structured JSON form of *event*."""
    kind = KINDS.get(event.kind)
    data: dict[str, Any] = {
        **event.vcs,
        "vcs.ref.head.revision": event.commit_sha,
        "vcs.ref.base.revision": event.base_commit_sha,
        "repowise.kind": event.kind,
        "repowise.component": event.component,
        "repowise.severity": event.severity,
        "repowise.stamp": event.stamp,
        "repowise.schema": event.schema,
        "evidence": dict(event.evidence),
    }
    out: dict[str, Any] = {
        "specversion": CLOUDEVENTS_SPECVERSION,
        "id": event.id,
        "source": event.source or f"/repos/{event.repo_id}",
        "type": kind.type if kind else f"dev.repowise.{event.kind}.0.1.0",
        "time": event.occurred_at.isoformat().replace("+00:00", "Z"),
        "datacontenttype": "application/json",
        "data": {k: v for k, v in data.items() if v is not None},
    }
    if event.component:
        out["subject"] = f"component:{event.component}"
    return out
