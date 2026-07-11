"""Harness adapters: one per coding agent whose transcripts we can read."""

from repowise.core.sessions.adapters.base import HarnessAdapter
from repowise.core.sessions.adapters.claude_code import ClaudeCodeAdapter

__all__ = ["ClaudeCodeAdapter", "HarnessAdapter"]
