"""Shared agent-session transcript layer.

One normalized :class:`Event` stream over coding-agent transcripts, behind a
:class:`HarnessAdapter` interface so consumers never parse harness-specific
JSONL themselves. Claude Code and Codex today, behind the same interface;
which of them a repository actually reads is a config question, not a
registry one.

Consumers are the distill transcript miners (missed savings, wasteful
re-reads, correction mining) and session decision mining, which rides the
same stream.

Everything here is read-only and local: transcripts are read from the user's
own machine and never leave it.
"""

from repowise.core.sessions.adapters import (
    INTENT_SHELL_CALLS,
    INTENT_TOOL_CALLS,
    INTENT_TURNS,
    ClaudeCodeAdapter,
    CodexAdapter,
    HarnessAdapter,
    RawPrefilter,
    get_adapter,
    register_adapter,
    registered_adapters,
)
from repowise.core.sessions.adapters.claude_code import transcript_dir_for
from repowise.core.sessions.cursor import CursorStore, iter_new_events
from repowise.core.sessions.events import (
    INTERRUPT_MARKER,
    Event,
    ToolResult,
    ToolUse,
    iter_deduped_usage,
    parse_timestamp,
)

__all__ = [
    "INTENT_SHELL_CALLS",
    "INTENT_TOOL_CALLS",
    "INTENT_TURNS",
    "INTERRUPT_MARKER",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "CursorStore",
    "Event",
    "HarnessAdapter",
    "RawPrefilter",
    "ToolResult",
    "ToolUse",
    "get_adapter",
    "iter_deduped_usage",
    "iter_new_events",
    "parse_timestamp",
    "register_adapter",
    "registered_adapters",
    "transcript_dir_for",
]
