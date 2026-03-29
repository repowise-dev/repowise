"""repowise MCP Server — 9 tools for AI coding assistants.

Exposes the full repowise wiki as queryable tools via the MCP protocol.
Supports both stdio transport (Claude Code, Cursor, Cline) and SSE transport
(web-based MCP clients).

Usage:
    repowise mcp --transport stdio  # for Claude Code / Cursor / Cline
    repowise mcp --transport sse    # for web-based clients
"""

from __future__ import annotations

import sys
from typing import Any

# --- Import submodules in dependency order (triggers tool registration) ---
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import (
    _build_origin_story,
    _compute_alignment,
    _get_repo,
    _is_path,
)
from repowise.server.mcp_server._server import (
    create_mcp_server,
    mcp,
    run_mcp,
)
from repowise.server.mcp_server.tool_context import get_context
from repowise.server.mcp_server.tool_dead_code import get_dead_code
from repowise.server.mcp_server.tool_decision_records import update_decision_records
from repowise.server.mcp_server.tool_dependency import (
    _build_visual_context,
    get_dependency_path,
)
from repowise.server.mcp_server.tool_diagram import get_architecture_diagram
from repowise.server.mcp_server.tool_overview import get_overview
from repowise.server.mcp_server.tool_risk import get_risk
from repowise.server.mcp_server.tool_search import search_codebase
from repowise.server.mcp_server.tool_why import get_why

# ---------------------------------------------------------------------------
# Backward-compatible access to _state globals.
#
# Test fixtures and some internal code do:
#     import repowise.server.mcp_server as mcp_mod
#     mcp_mod._session_factory = factory        # write
#     await mcp_mod._vector_store.search(...)    # read
#
# We proxy reads via module __getattr__ (PEP 562) and writes via a custom
# module __class__ override so that all mutations go to _state.
# ---------------------------------------------------------------------------

_STATE_NAMES = frozenset(
    {
        "_session_factory",
        "_vector_store",
        "_decision_store",
        "_fts",
        "_repo_path",
        "_vector_store_ready",
    }
)


def __getattr__(name: str) -> Any:
    if name in _STATE_NAMES:
        return getattr(_state, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_Module = type(sys.modules[__name__])


class _WritableModule(_Module):
    def __setattr__(self, name: str, value: Any) -> None:
        if name in _STATE_NAMES:
            setattr(_state, name, value)
        else:
            super().__setattr__(name, value)


sys.modules[__name__].__class__ = _WritableModule

__all__ = [
    "_build_origin_story",
    "_build_visual_context",
    "_compute_alignment",
    "_get_repo",
    "_is_path",
    "create_mcp_server",
    "get_architecture_diagram",
    "get_context",
    "get_dead_code",
    "get_dependency_path",
    "get_overview",
    "get_risk",
    "get_why",
    "mcp",
    "run_mcp",
    "search_codebase",
    "update_decision_records",
]
