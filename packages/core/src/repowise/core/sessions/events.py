"""The normalized transcript event model, harness-agnostic by design.

An :class:`Event` is one transcript line reduced to the fields miners care
about: who spoke, when, from where, what tools ran and what came back. The
shape is deliberately flat and tolerant; adapters populate whatever their
harness records and leave the rest at defaults, so a consumer written against
Events never needs to know which agent produced the transcript.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

#: Claude Code injects this text into the conversation when the user
#: interrupts a running turn; it is the strongest pushback signal a
#: transcript carries.
INTERRUPT_MARKER = "[Request interrupted by user"


@dataclass(slots=True)
class ToolUse:
    """One tool invocation block inside an assistant event."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass(slots=True)
class ToolResult:
    """One tool result block, paired to its invocation by ``tool_use_id``.

    ``content`` is the message-level result block content (what the model
    read back); ``payload`` is the harness-level result record when the
    transcript carries one (Claude Code's top-level ``toolUseResult``, whose
    string-vs-dict shape distinguishes command failures from successes).
    """

    tool_use_id: str
    is_error: bool = False
    content: Any = None
    payload: Any = None


@dataclass(slots=True)
class Event:
    """One normalized transcript line.

    ``kind`` is the harness's own entry type ("user", "assistant", "system",
    ...) passed through verbatim; consumers gate on the kinds they know.
    Timestamps are epoch seconds or None when the line carries none.
    """

    kind: str
    ts: float | None = None
    session_id: str | None = None
    cwd: str | None = None
    text: str = ""
    tool_uses: list[ToolUse] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    #: Model-reported token usage for this line, verbatim. The same usage
    #: object repeats on every content-block line of one API message; sum
    #: via :func:`iter_deduped_usage`, never directly.
    usage: dict[str, Any] | None = None
    #: The API message id (``message.id``), the usage dedup key.
    message_id: str | None = None
    #: Model that produced an assistant line (``message.model``).
    model: str | None = None
    #: True for subagent (sidechain) lines. Kept in the stream on purpose:
    #: subagent activity is real activity; consumers that only want the
    #: main thread filter it out themselves.
    sidechain: bool = False
    #: Harness-injected line, not something the user typed.
    is_meta: bool = False
    #: Post-compaction summary line; text is synthetic, skip for
    #: prompt-derived signals.
    is_compact_summary: bool = False

    @property
    def interrupted(self) -> bool:
        """True when this line records a user interrupt."""
        return INTERRUPT_MARKER in self.text


def iter_deduped_usage(events: Iterable[Event]) -> Iterator[Event]:
    """Events whose usage should be counted, one per API message.

    One API message spans several transcript lines (one per content block)
    and each repeats the full usage object; summing raw lines overcounts by
    roughly 2.6x. Yields only the first usage-bearing event per message id.
    """
    seen: set[str] = set()
    for event in events:
        if event.usage is None or event.message_id is None:
            continue
        if event.message_id in seen:
            continue
        seen.add(event.message_id)
        yield event
