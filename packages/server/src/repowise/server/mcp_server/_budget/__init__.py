"""Shared output-budget enforcement for MCP tools.

Every MCP tool that trims its response to fit the transport token cap goes
through this package instead of rolling its own silent drop. Two pieces:

* :func:`truncate_to_budget` — the staged whole-response truncation strategy
  (originally built for ``get_context``), now the reference implementation.
* :class:`OmissionCollector` — captures whatever a tool drops, persists it to
  the durable omission store, and stamps the response with a
  ``[repowise#<ref>]`` marker plus ``_meta.omitted`` so the content stays
  recoverable via ``repowise expand <ref>`` or ``get_symbol("repowise#<ref>")``.

Tools that don't use the staged truncator (fixed per-list caps) use the
collector directly at their cap sites.
"""

from __future__ import annotations

from repowise.server.mcp_server._budget.budgeter import (
    CHAR_BUDGET,
    CHARS_PER_TOKEN,
    TOKEN_BUDGET,
    estimate_response_tokens,
    truncate_to_budget,
)
from repowise.server.mcp_server._budget.collector import OmissionCollector

__all__ = [
    "CHARS_PER_TOKEN",
    "CHAR_BUDGET",
    "TOKEN_BUDGET",
    "OmissionCollector",
    "estimate_response_tokens",
    "truncate_to_budget",
]
