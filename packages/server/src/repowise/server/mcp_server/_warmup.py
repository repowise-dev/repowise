"""Build the served index's per-index caches while the server sits idle.

The first get_answer or search_codebase of a session used to build every
whole-repo projection it reads (file paths, symbol text, filename words, the
projected graph, file adjacency) inline: a first get_answer took 3.5 s on a
Django-size index and 11 s on an Envoy-size one, against about 1 s once they
exist. They depend only on the index, and the agent's
first call comes seconds after the handshake, so the server builds them in
that gap instead.

The work starts once the client has the ``initialize`` reply, runs on the
event loop one cache at a time, and yields between caches, so a request that
arrives meanwhile waits at most for the cache being built (which a first
question would have built anyway: :func:`~._graph_files.per_index` lets it
wait on the build in flight rather than repeat it). It is skipped when the
machine is short of free memory, since every MCP server on the machine holds
its own copy.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

_log = logging.getLogger("repowise.mcp")

#: Below this much free physical memory the caches are left to the first
#: question that needs them: an agent host can run several servers at once.
MIN_FREE_MEMORY_BYTES = 2 * 1024**3
#: Free memory asked for per byte of index store. The caches measured about a
#: quarter of the store (60 MB on a 240 MB Django index, 150 MB on a 655 MB
#: Envoy one), so twice the store keeps them well inside what is free. A size
#: proxy, not a measurement: a store that is mostly wiki prose over-asks.
FREE_MEMORY_PER_STORE_BYTE = 2

_task: asyncio.Task | None = None


async def _file_paths(session: Any, repo_id: str) -> Any:
    from repowise.server.mcp_server.tool_search_symbols import file_path_index

    return await file_path_index(session, repo_id)


async def _graph_paths(session: Any, repo_id: str) -> Any:
    from repowise.server.mcp_server._graph_files import graph_file_paths

    return await graph_file_paths(session, repo_id)


async def _symbol_text(session: Any, repo_id: str) -> Any:
    from repowise.server.mcp_server._prose_symbols import _symbol_text_index

    return await _symbol_text_index(session, repo_id)


async def _filename_words(session: Any, repo_id: str) -> Any:
    from repowise.server.mcp_server._prose_symbols import _filename_index

    return await _filename_index(session, repo_id)


async def _projected(session: Any, repo_id: str) -> Any:
    from repowise.server.mcp_server._answer_pipeline.graph_expand import projected_graph

    return await projected_graph(session, repo_id)


async def _adjacency(session: Any, repo_id: str) -> Any:
    from repowise.server.mcp_server._flow_path import file_adjacency

    return await file_adjacency(session, repo_id)


def _prime_git(path: str) -> None:
    """Run one ``git status`` so the first call finds git's index and the tree cached.

    A call reads the working tree's dirty paths under a 0.5 s bound
    (``_meta._working_tree_dirty_paths``); cold, the first read overran it and
    the call went out with the check "not evaluated". This read only warms the
    caches: its result is not kept, and ``--no-optional-locks`` keeps it from
    rewriting the index under a git command the user runs meanwhile.
    """
    import subprocess

    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            ["git", "--no-optional-locks", "-C", path, "--no-pager", "status", "--porcelain", "-z"],
            capture_output=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )


#: Every per-index cache a first get_answer or search_codebase builds, in the
#: order a question reads them.
WARM_STEPS: tuple[tuple[str, Callable[[Any, str], Awaitable[Any]]], ...] = (
    ("search_file_paths", _file_paths),
    ("graph_file_paths", _graph_paths),
    ("prose_symbol_text", _symbol_text),
    ("prose_filename_words", _filename_words),
    ("projected_graph", _projected),
    ("file_adjacency", _adjacency),
)


async def warm_index_caches() -> None:
    """Build every :data:`WARM_STEPS` cache for the served repository."""
    from repowise.core.persistence.database import get_session
    from repowise.server.mcp_server import _state
    from repowise.server.mcp_server._helpers import _get_repo, _resolve_repo_context

    if _state._serving_no_index:
        return
    ctx = await _resolve_repo_context(None)
    if ctx.session_factory is None:
        return
    started = time.perf_counter()
    if ctx.path is not None and _state.git_available() and (ctx.path / ".git").exists():
        await asyncio.to_thread(_prime_git, str(ctx.path))
    async with get_session(ctx.session_factory) as session:
        repo_id = (await _get_repo(session)).id
        for name, step in WARM_STEPS:
            began = time.perf_counter()
            await step(session, repo_id)
            _log.debug("repowise MCP: warmed %s in %.2fs", name, time.perf_counter() - began)
            await asyncio.sleep(0)  # let a waiting request in between caches
    _log.info("repowise MCP: index caches warm in %.1fs", time.perf_counter() - started)


async def _run() -> None:
    try:
        await warm_index_caches()
    except asyncio.CancelledError:
        raise
    except Exception:
        # A warmup is an optimisation: the first question builds what is missing.
        _log.debug("repowise MCP: cache warmup stopped", exc_info=True)


def _memory_needed() -> int:
    """Free memory the warmup asks for: the floor, or more for a large store."""
    from repowise.core.persistence.database import get_repo_db_path
    from repowise.server.mcp_server import _state

    store = 0
    if _state._repo_path:
        with contextlib.suppress(OSError):
            store = get_repo_db_path(_state._repo_path).stat().st_size
    return max(MIN_FREE_MEMORY_BYTES, FREE_MEMORY_PER_STORE_BYTE * store)


def start() -> None:
    """Start the warmup once per server, unless free memory is short."""
    global _task
    if _task is not None:
        return
    from repowise.core.procutils import available_memory_bytes

    free = available_memory_bytes()
    if free is not None and free < _memory_needed():
        _log.info("repowise MCP: skipping cache warmup, %d MB free", free // 2**20)
        return
    _task = asyncio.get_running_loop().create_task(_run(), name="index-cache-warmup")


async def stop() -> None:
    """Cancel a warmup still running; the next server start may warm again."""
    global _task
    task, _task = _task, None
    if task is not None and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
