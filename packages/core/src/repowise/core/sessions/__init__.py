"""Shared agent-session transcript layer.

One normalized :class:`Event` stream over coding-agent transcripts, behind a
:class:`HarnessAdapter` interface so consumers never parse harness-specific
JSONL themselves. Claude Code is the first adapter; Codex and others slot in
behind the same interface later.

Consumers today are the distill transcript miners (missed savings, wasteful
re-reads, correction mining). Session decision mining rides the same stream
next.

Everything here is read-only and local: transcripts are read from the user's
own machine and never leave it.
"""

from repowise.core.sessions.adapters.base import HarnessAdapter
from repowise.core.sessions.adapters.claude_code import (
    ClaudeCodeAdapter,
    parse_timestamp,
    transcript_dir_for,
)
from repowise.core.sessions.adapters.codex import CodexAdapter
from repowise.core.sessions.cursor import CursorStore, iter_new_events
from repowise.core.sessions.events import (
    INTERRUPT_MARKER,
    Event,
    ToolResult,
    ToolUse,
    iter_deduped_usage,
)

__all__ = [
    "INTERRUPT_MARKER",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "CursorStore",
    "Event",
    "HarnessAdapter",
    "ToolResult",
    "ToolUse",
    "iter_deduped_usage",
    "iter_new_events",
    "parse_timestamp",
    "transcript_dir_for",
]
