"""One answer to "which files is this file a graph neighbour of".

``imports`` joins file paths; ``calls`` and its siblings join ``path::Name``
symbol nodes, and nothing points from a symbol back to its file, so a consumer
keyed on paths sees only the first layer until it projects the second.

Projecting also manufactures a self-loop out of every intra-file call and
stitches files across a language boundary, so the guards live here with it.

A projection reads every edge of the repo, which is the slowest local step of
an answer, and its result only moves when the index does. :func:`per_index`
keeps one copy per index state so a question pays for the scan once.
"""

from __future__ import annotations

import asyncio
import os
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from sqlalchemy import select

from repowise.core.persistence.models import GraphNode, Repository
from repowise.server.mcp_server._index_state import index_state_key

_T = TypeVar("_T")

# repo_id -> (index state key, {projection name: value}), least recently used
# first. Bounded by repos because one entry holds whole-repo projections; a
# workspace server keeps its members warm up to this many.
_CACHE: OrderedDict[str, tuple[str, dict[str, Any]]] = OrderedDict()
_CACHE_MAX_REPOS = 8
# Concurrent cold questions would each pay the scan; one builds, the rest wait.
_LOCK = asyncio.Lock()

# Confidence floor for ``calls`` edges. Imports are always 1.0; calls average
# 0.90 with a low-confidence tail from heuristic resolution. 0.5 keeps every
# genuine call and drops the noise.
CALLS_CONF_FLOOR = 0.5


def is_symbol_node(node_id: str) -> bool:
    """Whether *node_id* addresses a symbol rather than a file."""
    return "::" in node_id


def node_to_file(node_id: str) -> str:
    """``a/b.py::Klass.meth`` -> ``a/b.py``. A file node id is returned unchanged."""
    return node_id.split("::", 1)[0]


def file_ext(path: str) -> str:
    """Lower-case file extension (``retrieval.py`` -> ``py``), ``""`` if none."""
    base = os.path.basename(path)
    return base.rsplit(".", 1)[1].lower() if "." in base else ""


def keep_projected_edge(
    src_file: str, tgt_file: str, edge_type: str | None, confidence: float | None
) -> bool:
    """Whether a symbol edge projected onto its two files is a real relation.

    Three guards, all load-bearing:

    * a ``calls`` edge below :data:`CALLS_CONF_FLOOR` is heuristic noise;
    * a self-loop is what the projection manufactures out of every intra-file
      call, and those outnumber the cross-file ones;
    * a cross-extension pair is a graph coincidence rather than dependency flow
      (a Python module and a same-named TypeScript re-export, a test naming both
      ends), and keeping them lets a walk stitch unrelated packages together.
    """
    if edge_type == "calls" and (confidence or 0.0) < CALLS_CONF_FLOOR:
        return False
    if not src_file or not tgt_file or src_file == tgt_file:
        return False
    return file_ext(src_file) == file_ext(tgt_file)


def reset_cache() -> None:
    """Drop every cached projection. For tests and for a re-index in-process."""
    global _LOCK
    _CACHE.clear()
    # A lock that once waited is bound to that event loop; a fresh one is not.
    _LOCK = asyncio.Lock()


def _cached(repo_id: str, key: str, name: str) -> Any | None:
    entry = _CACHE.get(repo_id)
    if entry is None or entry[0] != key or name not in entry[1]:
        return None
    _CACHE.move_to_end(repo_id)
    return entry[1][name]


async def per_index(
    session: Any, repo_id: str, name: str, build: Callable[[], Awaitable[_T]]
) -> _T:
    """``await build()``, reused until this repo's index changes.

    Keyed by :func:`index_state_key`, shared with the server's other per-index
    caches. Callers must treat the result as read-only: it is shared across
    questions.
    """
    repository = (
        await session.execute(select(Repository).where(Repository.id == repo_id))
    ).scalar_one_or_none()
    key = index_state_key(repository)
    hit = _cached(repo_id, key, name)
    if hit is not None:
        return hit
    async with _LOCK:
        hit = _cached(repo_id, key, name)
        if hit is not None:
            return hit
        value = await build()
        entry = _CACHE.get(repo_id)
        if entry is None or entry[0] != key:
            # An older index state of this repo is never asked for again.
            entry = (key, {})
            _CACHE[repo_id] = entry
        entry[1][name] = value
        _CACHE.move_to_end(repo_id)
        while len(_CACHE) > _CACHE_MAX_REPOS:
            _CACHE.popitem(last=False)
        return value


async def graph_file_paths(session: Any, repo_id: str) -> tuple[str, ...]:
    """Every indexed file's path, with or without a wiki page. Read-only."""

    async def build() -> tuple[str, ...]:
        res = await session.execute(
            select(GraphNode.node_id).where(
                GraphNode.repository_id == repo_id,
                GraphNode.node_type == "file",
                ~GraphNode.node_id.startswith("external:"),
            )
        )
        return tuple(sorted(p for (p,) in res.all() if p))

    return await per_index(session, repo_id, "graph_file_paths", build)
