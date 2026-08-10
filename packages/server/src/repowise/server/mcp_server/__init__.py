"""repowise MCP Server — a curated, configurable tool surface for AI agents.

By default a single-repo server exposes eleven tools (get_answer, get_context,
get_symbol, search_codebase, get_overview, get_risk, get_change_risk, get_why,
get_dead_code, get_health, list_repos); two more (get_blast_radius, get_architecture) are added
automatically in workspace mode. Four further tools (get_dependency_path,
get_execution_flows, generate_refactoring_code, get_conformance) are registered
but off by default and can be opted in via the ``mcp.tools`` config block or the
``repowise mcp --tools`` flag; get_conformance only does useful work in workspace
mode. The selection layer lives in :mod:`._tool_selection`.

Exposes the full repowise wiki as queryable tools via the MCP protocol.
Supports stdio transport (Claude Code, Cursor, Cline), streamable HTTP, and
legacy SSE transport.

Tool modules load lazily (see :func:`ensure_full_surface`), so importing this
package, or one tool out of it, does not drag in the other sixteen.

Usage:
    repowise mcp --transport stdio  # for Claude Code / Cursor / Cline
    repowise mcp --transport streamable-http  # for HTTP clients
    repowise mcp --transport sse    # for legacy SSE clients
"""

from __future__ import annotations

import importlib
import sys
from typing import Any

# ``_state`` is the one submodule that stays eager: every other module in this
# package reads its globals, and it defers its own sqlalchemy import so the
# whole package now costs about what a bare interpreter does.
from repowise.server.mcp_server import _state

# ---------------------------------------------------------------------------
# Lazy attribute surface (PEP 562).
#
# This package used to import all 17 tool modules — and through them
# core.analysis.health, core.generation.onboarding, FastMCP, LanceDB's
# dependency chain — at package import. That made importing any ONE tool cost
# ~2.5s, roughly 9x the entire CLI, because a leaf import initialises the
# parent package first. ``repowise.cli.main`` solved the same shape for its
# ~35 command modules with lazy registration; this is that fix for the tool
# surface, so a CLI command adapting a single MCP tool pays for that tool only.
#
# Every name below resolves on first attribute access and is then cached on the
# module, so ``from repowise.server.mcp_server import get_answer`` reads exactly
# as it did before.
# ---------------------------------------------------------------------------

#: ``tool name -> submodule``. The full set is what ``ensure_full_surface``
#: imports; individually they are what a single-tool consumer pays for.
_TOOL_MODULES: dict[str, str] = {
    "generate_refactoring_code": "tool_refactoring",
    "get_answer": "tool_answer",
    "get_architecture": "tool_architecture",
    "get_blast_radius": "tool_blast_radius",
    "get_change_risk": "tool_change_risk",
    "get_conformance": "tool_conformance",
    "get_context": "tool_context",
    "get_dead_code": "tool_dead_code",
    "get_dependency_path": "tool_dependency",
    "get_execution_flows": "tool_flows",
    "get_health": "tool_health",
    "get_overview": "tool_overview",
    "get_risk": "tool_risk",
    "get_symbol": "tool_symbol",
    "get_why": "tool_why",
    "list_repos": "tool_repos",
    "search_codebase": "tool_search",
}

#: ``exported name -> (submodule, name within it)`` for the non-tool re-exports.
_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "_build_origin_story": ("_helpers", "_build_origin_story"),
    "_build_visual_context": ("_graph_utils", "build_visual_context"),
    "_compute_alignment": ("_helpers", "_compute_alignment"),
    "_get_repo": ("_helpers", "_get_repo"),
    "_is_path": ("_helpers", "_is_path"),
    "create_mcp_server": ("_server", "create_mcp_server"),
    "run_mcp": ("_server", "run_mcp"),
}

_surface_applied = False


def ensure_full_surface() -> Any:
    """Import every tool module and attach the registry to the FastMCP server.

    This is the work that used to happen at package import. It is what makes
    ``mcp`` a *populated* server rather than a bare instance, so every caller
    that reads the advertised tool set has to go through here first:
    ``create_mcp_server`` / ``run_mcp``, the ``mcp`` attribute itself, and the
    selection layer (``apply_tool_selection`` / ``describe_tool_surface``,
    which the dashboard's ``/mcp`` routes import directly).

    Idempotent, and returns the server instance so callers need not re-import
    it. Note that ``mcp_tool_registry.apply`` is itself a no-op on a repeat
    call for the same server.
    """
    global _surface_applied
    from repowise.server.mcp_server._server import mcp as _mcp

    if _surface_applied:
        return _mcp

    for module in dict.fromkeys(_TOOL_MODULES.values()):
        importlib.import_module(f"{__name__}.{module}")

    from repowise.core.registry import mcp_tool_registry
    from repowise.server.mcp_server._failure_shield import shield as _failure_shield
    from repowise.server.mcp_server._savings import instrument as _savings_instrument
    from repowise.server.mcp_server._tool_selection import snapshot_full_surface

    # ``middleware`` wraps each tool twice, both layers signature-preserving so
    # the tool schemas are unchanged. The failure shield sits innermost: no
    # exception may escape to FastMCP as a protocol-level isError (an early
    # isError teaches the agent to abandon the server for the whole session).
    # The savings layer wraps it, so shaped error responses are still
    # dead-end-debited in the ledger.
    mcp_tool_registry.apply(
        _mcp, middleware=lambda fn: _savings_instrument(_failure_shield(fn))
    )

    # Snapshot the full registered surface so per-server tool selection
    # (single-repo vs workspace, config/CLI overrides) can rebuild from it.
    # Selection itself runs later, once the repo path is known.
    snapshot_full_surface(_mcp)

    _surface_applied = True
    return _mcp


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
        "_lancedb_ready",
        "_registry",
        "_workspace_root",
        "_cross_repo_enricher",
        "_embedder_status",
    }
)


def __getattr__(name: str) -> Any:
    # State names proxy through to _state on every read — they are mutable and
    # must never be cached onto this module.
    if name in _STATE_NAMES:
        return getattr(_state, name)

    if name == "mcp":
        # Reading ``mcp`` means reading the advertised tool set, so the surface
        # has to exist by the time it is handed over.
        value: Any = ensure_full_surface()
    elif name in _TOOL_MODULES:
        value = getattr(
            importlib.import_module(f"{__name__}.{_TOOL_MODULES[name]}"), name
        )
    elif name in _LAZY_ATTRS:
        module_name, attr = _LAZY_ATTRS[name]
        value = getattr(importlib.import_module(f"{__name__}.{module_name}"), attr)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    setattr(sys.modules[__name__], name, value)
    return value


def __dir__() -> list[str]:
    """List the lazy names too, which ``__getattr__`` alone does not.

    PEP 562 pairs the two for exactly this reason: without it a name resolves
    fine through ``getattr`` but is invisible to ``dir()``, REPL completion, and
    any ``name in dir(module)`` check.
    """
    return sorted(
        set(globals()) | set(_STATE_NAMES) | set(_TOOL_MODULES) | set(_LAZY_ATTRS) | {"mcp"}
    )


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
    "ensure_full_surface",
    "generate_refactoring_code",
    "get_answer",
    "get_architecture",
    "get_blast_radius",
    "get_change_risk",
    "get_conformance",
    "get_context",
    "get_dead_code",
    "get_dependency_path",
    "get_execution_flows",
    "get_health",
    "get_overview",
    "get_risk",
    "get_symbol",
    "get_why",
    "list_repos",
    "mcp",
    "run_mcp",
    "search_codebase",
]
