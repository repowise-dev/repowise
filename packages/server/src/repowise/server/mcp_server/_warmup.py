"""Build the served index's per-index caches while the server sits idle.

The first get_answer or search_codebase of a session used to build every
whole-repo projection it reads (file paths, symbol text, filename words, the
projected graph, file adjacency) inline: a first get_answer took 3.5 s on a
Django-size index and 11 s on an Envoy-size one, against about 1 s once they
exist. They depend only on the index, and the agent's first call comes
seconds after the handshake, so the server builds them in that gap instead.

The work starts once the client has the ``initialize`` reply and runs on the
event loop one cache at a time. The first tool call stops it after the cache
in flight; :func:`~._graph_files.per_index` locks per cache, so that call
waits on the warm build only when it reads the same cache, and then reuses
it. From then on each call builds what it reads, as before. It is skipped when the machine
is short of free memory, since every MCP server on the machine holds its own
copy, and when ``performance.warm_caches`` is off.
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
_call_seen = False


def call_started() -> None:
    """Note a tool call: the warmup stops after the cache it is building."""
    global _call_seen
    _call_seen = True


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
    from repowise.server.mcp_server import _state
    from repowise.server.mcp_server._helpers import _resolve_repo_context

    if _state._serving_no_index:
        return
    ctx = await _resolve_repo_context(None)
    if ctx.session_factory is None:
        return
    started = time.perf_counter()
    if await _build_caches(ctx):
        _log.info("repowise MCP: index caches warm in %.1fs", time.perf_counter() - started)


async def _build_caches(ctx: Any) -> bool:
    """Build the caches until a tool call starts; whether every one was built."""
    from repowise.core.persistence.database import get_session
    from repowise.server.mcp_server._helpers import _get_repo

    async with get_session(ctx.session_factory) as session:
        repo_id = (await _get_repo(session)).id
    for name, step in WARM_STEPS:
        if _call_seen:
            # The call builds what it reads itself; more warm work would only
            # compete with it for the loop.
            return False
        began = time.perf_counter()
        # A session per cache: one read snapshot held across every step would
        # keep keying them to an index a re-index has since replaced.
        async with get_session(ctx.session_factory) as session:
            await step(session, repo_id)
        _log.debug("repowise MCP: warmed %s in %.2fs", name, time.perf_counter() - began)
        await asyncio.sleep(0)  # let a waiting request in between caches
    return True


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


def _turned_off() -> bool:
    """``performance.warm_caches: false`` in the served repository's config.yaml.

    An unreadable config warms as the default does: the warmup is an
    optimisation, and a broken file is ``repowise doctor``'s to report.
    """
    from repowise.server.mcp_server import _state

    if not _state._repo_path:
        return False
    try:
        from repowise.core.settings.writers import current

        return current(_state._repo_path, "performance.warm_caches").value is False
    except Exception:
        return False


def start() -> None:
    """Start the warmup once per server, unless turned off or free memory is short."""
    global _task, _call_seen
    if _task is not None:
        return
    if _turned_off():
        _log.info("repowise MCP: skipping cache warmup, performance.warm_caches is off")
        return
    from repowise.core.procutils import available_memory_bytes

    free = available_memory_bytes()
    if free is not None and free < _memory_needed():
        _log.info("repowise MCP: skipping cache warmup, %d MB free", free // 2**20)
        return
    _call_seen = False
    _task = asyncio.get_running_loop().create_task(_run(), name="index-cache-warmup")


async def stop() -> None:
    """Cancel a warmup still running; the next server start may warm again."""
    global _task, _call_seen
    task, _task = _task, None
    _call_seen = False
    if task is not None and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
