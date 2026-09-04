"""Prose spans: the input the broad discovery call is grounded in.

A span is one user or assistant turn from a transcript delta, clipped, given a
stable id, and queued in the session staging store. Spans are collected by
teeing the event stream the decision miner is already reading, never by a
second transcript read: the cursor advances as that read happens, so a second
pass would find nothing left.
"""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from repowise.core.sessions.events import (
    Event,
    event_files,
    is_prose_user_text,
    relative_files,
)
from repowise.core.sessions.staging import SessionStagingStore

__all__ = ["ProseSpan", "SpanCollector", "span_id_for"]

#: Characters kept per span. Long assistant turns are head/tail clipped rather
#: than truncated: the reasoning that opens a turn and the conclusion that
#: closes it are both load-bearing, and the middle is usually the diff.
_SPAN_CHARS = 1_600
_SPAN_HEAD = 1_100
_SPAN_TAIL = 400
_ELLIPSIS = "\n[...]\n"

#: A turn shorter than this carries no decision, only acknowledgement.
_MIN_SPAN_CHARS = 40

#: Spans kept per session per update. One very long session must not crowd
#: every other session out of the queue head.
_MAX_SPANS_PER_SESSION = 60

#: Recently touched files offered alongside a span. A prose turn almost never
#: carries tool inputs of its own, so pricing scope on the turn alone leaves
#: every candidate repository-wide; this is the menu the model may select
#: from. It is deliberately only a menu: nothing here becomes scope unless the
#: model picks it, so an unrelated file that happened to be open cannot pin a
#: repository-wide rule to itself.
_NEARBY_FILES = 6


def _clip(text: str) -> str:
    text = text.strip()
    if len(text) <= _SPAN_CHARS:
        return text
    return text[:_SPAN_HEAD] + _ELLIPSIS + text[-_SPAN_TAIL:]


def span_id_for(session_id: str, ts: float | None, text: str) -> str:
    """A stable id for one span, so a re-read stages it exactly once."""
    payload = f"{session_id}|{ts or 0.0:.3f}|{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class ProseSpan:
    """One clipped transcript turn, as the packet and the grounder see it."""

    span_id: str
    session_id: str
    role: str
    text: str
    files: tuple[str, ...]
    ts: float | None

    @classmethod
    def from_row(cls, row: dict) -> ProseSpan:
        return cls(
            span_id=row["span_id"],
            session_id=row["session_id"],
            role=row["role"],
            text=row["text"],
            files=tuple(row["files"]),
            ts=row["ts"],
        )


class SpanCollector:
    """A pass-through tee that queues eligible prose as it streams by.

    Writes straight into the staging store rather than accumulating in memory:
    the queue is the durable backlog, and it commits with the cursors that
    said those bytes were read.
    """

    def __init__(
        self,
        store: SessionStagingStore,
        repo_root: Path,
        *,
        now: float | None = None,
    ) -> None:
        self._store = store
        self._repo_root = repo_root
        self._repo_prefix = str(repo_root).lower().rstrip("\\/")
        self._now = now
        self._per_session: dict[str, int] = {}
        self._nearby: dict[str, deque[str]] = {}
        self.queued = 0

    def observe(self, events: Iterable[Event]) -> Iterator[Event]:
        for event in events:
            self._maybe_queue(event)
            yield event

    def _maybe_queue(self, event: Event) -> None:
        session_id = event.session_id
        if not session_id:
            return
        # Same repository scoping as the deterministic gates: one checkout can
        # host nested repositories, and a span from one must not be evidence
        # for another.
        cwd = (event.cwd or "").lower().rstrip("\\/")
        if cwd and not cwd.startswith(self._repo_prefix):
            return
        nearby = self._nearby.setdefault(session_id, deque(maxlen=_NEARBY_FILES))
        for path in relative_files(event_files(event), self._repo_root):
            if path in nearby:
                nearby.remove(path)
            nearby.append(path)

        if event.kind == "user":
            if not is_prose_user_text(event):
                return
        elif event.kind == "assistant":
            if event.sidechain or event.is_meta or event.is_compact_summary:
                return
        else:
            return
        text = _clip(event.text)
        if len(text) < _MIN_SPAN_CHARS:
            return
        seen = self._per_session.get(session_id, 0)
        if seen >= _MAX_SPANS_PER_SESSION:
            return
        self._per_session[session_id] = seen + 1
        if self._store.add_discovery_span(
            span_id=span_id_for(session_id, event.ts, text),
            session_id=session_id,
            role=event.kind,
            text=text,
            files=list(nearby),
            ts=event.ts,
            now=self._now,
        ):
            self.queued += 1
