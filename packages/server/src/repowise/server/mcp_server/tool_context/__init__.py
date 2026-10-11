"""get_context MCP tool — relationships and triage signals.

Façade: the tool used to be a single 1213-line ``tool_context.py``. It is now
a package (orchestrator + targets / enrichment / kg / truncation). Importers
keep working unchanged — ``from ...tool_context import get_context`` resolves,
importing the package registers the tool via ``@mcp.tool()``, and the
truncation helpers (used by tests) remain importable from the package root.
"""

from __future__ import annotations

from repowise.server.mcp_server.tool_context.context import get_context
from repowise.server.mcp_server.tool_context.truncation import (
    _CHAR_BUDGET,
    _TOKEN_BUDGET,
    _estimate_tokens,
    _truncate_to_budget,
)

__all__ = [
    "_CHAR_BUDGET",
    "_TOKEN_BUDGET",
    "_estimate_tokens",
    "_truncate_to_budget",
    "get_context",
]
