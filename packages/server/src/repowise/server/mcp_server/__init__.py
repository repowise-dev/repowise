"""repowise MCP Server — a curated, configurable tool surface for AI agents.

By default a single-repo server exposes ten tools (get_answer, get_context,
get_symbol, search_codebase, get_overview, get_risk, get_why, get_dead_code,
get_health, list_repos); three more (get_blast_radius, get_conformance,
get_architecture) are added automatically in workspace mode. Two further tools
(get_dependency_path, get_execution_flows) are registered but off by default and
can be opted in via the ``mcp.tools`` config block or the ``repowise mcp
--tools`` flag. The selection layer lives in :mod:`._tool_selection`.

Exposes the full repowise wiki as queryable tools via the MCP protocol.
Supports stdio transport (Claude Code, Cursor, Cline), streamable HTTP, and
legacy SSE transport.

Usage:
    repowise mcp --transport stdio  # for Claude Code / Cursor / Cline
    repowise mcp --transport streamable-http  # for HTTP clients
    repowise mcp --transport sse    # for legacy SSE clients
"""

from __future__ import annotations

import sys
from typing import Any

# Attach every tool that registered itself through the shared registry to
# the FastMCP instance. Idempotent per server, so a second call (e.g. when
# tests build an isolated mcp) is a no-op against the original mcp.
from repowise.core.registry import mcp_tool_registry as _mcp_tool_registry

# --- Import submodules in dependency order (triggers tool registration) ---
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._graph_utils import (  # used by routers/graph.py
    build_visual_context as _build_visual_context,
)
from repowise.server.mcp_server._helpers import (
    _build_origin_story,
    _compute_alignment,
    _get_repo,
    _is_path,
)
from repowise.server.mcp_server._savings import instrument as _savings_instrument
from repowise.server.mcp_server._server import (
    create_mcp_server,
    mcp,
    run_mcp,
)
from repowise.server.mcp_server._tool_selection import (
    snapshot_full_surface as _snapshot,
)
from repowise.server.mcp_server.tool_answer import get_answer
from repowise.server.mcp_server.tool_architecture import get_architecture
from repowise.server.mcp_server.tool_blast_radius import get_blast_radius
from repowise.server.mcp_server.tool_conformance import get_conformance
from repowise.server.mcp_server.tool_context import get_context
from repowise.server.mcp_server.tool_dead_code import get_dead_code
from repowise.server.mcp_server.tool_dependency import get_dependency_path
from repowise.server.mcp_server.tool_flows import get_execution_flows
from repowise.server.mcp_server.tool_health import get_health
from repowise.server.mcp_server.tool_overview import get_overview
from repowise.server.mcp_server.tool_refactoring import generate_refactoring_code
from repowise.server.mcp_server.tool_repos import list_repos
from repowise.server.mcp_server.tool_risk import get_risk
from repowise.server.mcp_server.tool_search import search_codebase
from repowise.server.mcp_server.tool_symbol import get_symbol
from repowise.server.mcp_server.tool_why import get_why

# ``middleware`` wraps each tool with savings instrumentation: every call records
# the counterfactual raw-exploration tokens its answer replaced into the unified
# ledger. The wrapper is signature-preserving, so tool schemas are unchanged.
_mcp_tool_registry.apply(mcp, middleware=_savings_instrument)

# Snapshot the full registered surface so per-server tool selection (single-repo
# vs workspace, config/CLI overrides) can rebuild from it. Selection itself runs
# later, in create_mcp_server / run_mcp, once the repo path is known.
_snapshot(mcp)

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
        "_registry",
        "_workspace_root",
        "_cross_repo_enricher",
        "_embedder_status",
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
    "get_answer",
    "get_architecture",
    "get_blast_radius",
    "get_conformance",
    "get_context",
    "get_dead_code",
    "get_dependency_path",
    "get_execution_flows",
    "get_health",
    "generate_refactoring_code",
    "get_overview",
    "get_risk",
    "get_symbol",
    "get_why",
    "list_repos",
    "mcp",
    "run_mcp",
    "search_codebase",
]
