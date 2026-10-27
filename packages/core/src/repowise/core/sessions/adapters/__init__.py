"""Harness adapters: one per coding agent whose transcripts we can read.

Importing this package is what registers the built-in adapters; a new
harness adds its import here and nothing else changes.
"""

from repowise.core.sessions.adapters.base import (
    INTENT_SHELL_CALLS,
    INTENT_TOOL_CALLS,
    INTENT_TURNS,
    HarnessAdapter,
    RawPrefilter,
)
from repowise.core.sessions.adapters.claude_code import ClaudeCodeAdapter
from repowise.core.sessions.adapters.codex import CodexAdapter
from repowise.core.sessions.adapters.registry import (
    get_adapter,
    register_adapter,
    registered_adapters,
)

__all__ = [
    "INTENT_SHELL_CALLS",
    "INTENT_TOOL_CALLS",
    "INTENT_TURNS",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "HarnessAdapter",
    "RawPrefilter",
    "get_adapter",
    "register_adapter",
    "registered_adapters",
]
