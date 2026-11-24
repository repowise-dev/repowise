"""Stable names for a host that serves the MCP tools inside its own process.

Build a context with :func:`embedded_context`, wrap a tool with
:func:`tool_middleware`, then call it inside ``with serving(ctx):``. Pass
``telemetry=False`` when the host does its own usage accounting.

The engine keeps whole-repository projections in process-wide caches.
:func:`release` drops one repository's entries when the host stops serving it,
and :func:`set_repo_cap` bounds how many repositories those caches hold.
"""

from __future__ import annotations

from repowise.core.persistence.crud.analysis.fix_first import drop_fix_first_repo
from repowise.core.persistence.index_cache import drop_repo_everywhere
from repowise.core.workspace.registry import embedded_context
from repowise.server.mcp_server import _basis, _components, _graph_files, tool_middleware
from repowise.server.mcp_server._state import request_context, serving
from repowise.server.mcp_server.tool_overview import graph as _overview_graph

__all__ = [
    "embedded_context",
    "release",
    "request_context",
    "serving",
    "set_repo_cap",
    "tool_middleware",
]

# The caches that hold whole-repository projections, each bounded by its own limit.
_REPO_CACHES = (_graph_files, _overview_graph, _components, _basis)


def release(repo_id: str) -> None:
    """Drop every entry the engine's in-process caches hold for *repo_id*.

    Other repositories' entries stay, and so do sealed copies on disk. Call it on the serving event loop: the
    caches are loop-confined, so it runs between other calls' steps and never
    inside one. A call already in flight for *repo_id* may add its entry back.
    """
    for cache in _REPO_CACHES:
        cache.drop_repo(repo_id)
    drop_repo_everywhere(repo_id)
    drop_fix_first_repo(repo_id)


def set_repo_cap(repos: int) -> None:
    """Hold at most *repos* entries in each whole-repository cache.

    An entry is one repository at one index state (a component or dependency
    entry also varies with exclusion rules), so the cap bounds repositories
    held. Each cache keeps its own eviction (least recently used, oldest
    first, or clear when full) and trims to the cap now. Call it on the
    serving event loop, like :func:`release`. Without a call the limits stay
    as shipped: 8, 16, 16 and 32 entries.
    """
    if repos < 1:
        raise ValueError("repos must be at least 1")
    for cache in _REPO_CACHES:
        cache.set_limit(repos)
