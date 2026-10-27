"""Keep the model-facing chat request inside the model's context window.

Stored and streamed artifacts stay whole so a client can render them; only the
copy sent to the provider is bounded. History is cut to a newest suffix of
whole turns, an oversized tool result becomes a marked excerpt, and a request
still over the ceiling sheds its oldest complete tool rounds (an assistant tool
call and its results move together, or the provider rejects the transcript).

The ceilings sit above what the MCP tools already return (their own response
budget is about 32k characters), so an ordinary turn reaches the model exactly
as before; they only bite on long conversations and runaway loops. Characters,
not tokens: no tokenizer is shipped, and dense JSON costs roughly 2.5 to 4
characters a token. When a provider still reports an overflow the engine retries
once with :func:`compose_request` in emergency mode.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

#: One tool result as the model sees it.
RESULT_CHAR_BUDGET = 48_000
#: Earlier turns replayed into a new turn, tool results included.
HISTORY_CHAR_BUDGET = 240_000
#: The whole message list of one provider call (system prompt and schemas excluded).
REQUEST_CHAR_BUDGET = 400_000

OVERFLOW_MESSAGE = (
    "This conversation is too large for the model to process. "
    "Ask a narrower question or start a new chat."
)

_MARKER = "\n\n[content omitted to fit the model context]\n\n"
_OVERFLOW_MARKERS = (
    "context_length_exceeded",
    "context length",
    "context window",
    "input tokens exceed",
    "maximum context length",
    "prompt is too long",
    "request too large",
    "too many tokens",
)


def message_chars(value: Any) -> int:
    """Compact JSON size, the unit every budget here is counted in."""
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str))


def _middle_truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    available = max(0, limit - len(_MARKER))
    head = available // 2
    return f"{text[:head]}{_MARKER}{text[len(text) - (available - head):]}"


def _bounded_tool_message(message: dict[str, Any], limit: int) -> dict[str, Any]:
    content = message.get("content")
    if message.get("role") != "tool" or not isinstance(content, str) or len(content) <= limit:
        return message
    excerpt = {
        "truncated": True,
        "original_chars": len(content),
        "note": "Result too large for the chat context; ask for a narrower target.",
        "excerpt": _middle_truncate(content, max(1_000, limit - 400)),
    }
    return {**message, "content": json.dumps(excerpt)}


def bound_tool_messages(
    messages: Sequence[dict[str, Any]], limit: int = RESULT_CHAR_BUDGET
) -> list[dict[str, Any]]:
    """The messages with every oversized tool result replaced by an excerpt.

    Parallel results of one round share the budget, so a round of five large
    reads costs about what one does.
    """
    results = sum(1 for m in messages if m.get("role") == "tool")
    share = max(4_000, limit // max(1, results))
    return [_bounded_tool_message(m, share) for m in messages]


def _turn_groups(messages: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split a transcript into turns, each starting at a user message."""
    groups: list[list[dict[str, Any]]] = []
    for message in messages:
        if message.get("role") == "user" or not groups:
            groups.append([])
        groups[-1].append(message)
    return groups


def bound_history(
    messages: Sequence[dict[str, Any]], budget: int = HISTORY_CHAR_BUDGET
) -> list[dict[str, Any]]:
    """The newest whole turns that fit *budget*, oldest first.

    The last turn (the question being asked) is always kept; a huge question
    is middle-truncated rather than dropped. A suffix never starts with an
    orphaned assistant or tool message.
    """
    groups = _turn_groups(messages)
    kept: list[list[dict[str, Any]]] = []
    remaining = budget
    for index, group in enumerate(reversed(groups)):
        group = bound_tool_messages(group)
        size = message_chars(group)
        if index == 0:
            if size > remaining:
                group = [_bounded_user_message(m, remaining) for m in group]
            kept.append(group)
            remaining -= min(remaining, size)
            continue
        if size > remaining:
            break
        kept.append(group)
        remaining -= size
    selected = [message for group in reversed(kept) for message in group]
    while selected and selected[0].get("role") != "user":
        selected.pop(0)
    return selected


def _bounded_user_message(message: dict[str, Any], limit: int) -> dict[str, Any]:
    content = message.get("content")
    if message.get("role") != "user" or not isinstance(content, str):
        return message
    return {**message, "content": _middle_truncate(content, max(1_000, limit))}


def _flatten(base: Sequence[dict[str, Any]], rounds: Sequence[Sequence[dict[str, Any]]]):
    return [*base, *(message for round_messages in rounds for message in round_messages)]


def compose_request(
    base: Sequence[dict[str, Any]],
    rounds: Sequence[Sequence[dict[str, Any]]],
    *,
    emergency: bool = False,
) -> list[dict[str, Any]]:
    """One provider call's messages: history plus this turn's tool rounds.

    Sheds the oldest complete rounds while the request is over budget. In
    emergency mode (after the provider reported an overflow) only the newest
    question and the newest round are sent.
    """
    rounds = [bound_tool_messages(r) for r in rounds]
    if emergency:
        newest_user = next((m for m in reversed(base) if m.get("role") == "user"), None)
        return _flatten([newest_user] if newest_user else [], rounds[-1:])
    messages = _flatten(base, rounds)
    while len(rounds) > 1 and message_chars(messages) > REQUEST_CHAR_BUDGET:
        rounds.pop(0)
        messages = _flatten(base, rounds)
    return messages


def is_context_overflow(exc: BaseException) -> bool:
    """True when a provider error says the request exceeded the context window."""
    text = str(exc).lower()
    return any(marker in text for marker in _OVERFLOW_MARKERS)
