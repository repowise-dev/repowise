"""Shared output-budget enforcement for MCP tools.

Every MCP tool that trims its response to fit the transport token cap goes
through this package instead of rolling its own silent drop. Three pieces:

* :func:`truncate_to_budget` — the staged whole-response truncation strategy
  built for (and shaped by) ``get_context``'s targets/docs/symbols payload.
* :func:`fit_to_budget` — the whole-response ceiling for tools whose payload is
  a bag of independent blocks: sheds them in a tool-declared order until the
  response fits.
* :class:`OmissionCollector` — captures whatever a tool drops, persists it to
  the durable omission store, and stamps the response with a
  ``[repowise#<ref>]`` marker plus ``_meta.omitted`` so the content stays
  recoverable via ``repowise expand <ref>`` or ``get_symbol("repowise#<ref>")``.

Tools with fixed per-list caps use the collector directly at their cap sites,
under whichever whole-response ceiling they enforce.
"""

from __future__ import annotations

from repowise.server.mcp_server._budget.budgeter import (
    CHAR_BUDGET,
    CHARS_PER_TOKEN,
    FIT_HEADROOM_CHARS,
    HOST_CAP_BUDGET_FRACTION,
    HOST_MCP_TOKEN_CAP_DEFAULT,
    TOKEN_BUDGET,
    effective_char_budget,
    estimate_response_tokens,
    fit_to_budget,
    host_token_cap,
    over_budget,
    truncate_to_budget,
)
from repowise.server.mcp_server._budget.collector import OmissionCollector, cap_collection
from repowise.server.mcp_server._budget.contracts import (
    DEFAULT_RESPONSE_CHARS,
    EXPANDED_RESPONSE_CHARS,
    budgeted_tool_names,
    enforce_response_budget,
    resolve_response_budget_repo_root,
)

__all__ = [
    "CHARS_PER_TOKEN",
    "CHAR_BUDGET",
    "DEFAULT_RESPONSE_CHARS",
    "EXPANDED_RESPONSE_CHARS",
    "FIT_HEADROOM_CHARS",
    "HOST_CAP_BUDGET_FRACTION",
    "HOST_MCP_TOKEN_CAP_DEFAULT",
    "TOKEN_BUDGET",
    "OmissionCollector",
    "budgeted_tool_names",
    "cap_collection",
    "effective_char_budget",
    "enforce_response_budget",
    "estimate_response_tokens",
    "fit_to_budget",
    "host_token_cap",
    "over_budget",
    "resolve_response_budget_repo_root",
    "truncate_to_budget",
]
