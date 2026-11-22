"""One answer to "which files is this file a graph neighbour of".

``imports`` joins file paths; ``calls`` and its siblings join ``path::Name``
symbol nodes, and nothing points from a symbol back to its file, so a consumer
keyed on paths sees only the first layer until it projects the second.

Projecting also manufactures a self-loop out of every intra-file call and
stitches files across a language boundary, so the guards live here with it.

A projection reads every edge of the repo, which is the slowest local step of
an answer, and its result only moves when the index does. :func:`per_index`
keeps one copy per index state so a question pays for the scan once, and a
sealed copy beside the index so the next server process loads it instead of
scanning again.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import select

from repowise.core.ingestion.symbol_identity import file_of_symbol
from repowise.core.persistence.models import GraphNode, Repository
from repowise.server.mcp_server._index_state import index_state_key

_T = TypeVar("_T")
_log = logging.getLogger("repowise.mcp")

# Kept importable under its old name for code outside this package.
node_to_file = file_of_symbol

# repo_id -> (index state key, {projection name: value}), least recently used
# first. Bounded by repos because one entry holds whole-repo projections; a
# workspace server keeps its members warm up to this many.
_CACHE: OrderedDict[str, tuple[str, dict[str, Any]]] = OrderedDict()
_CACHE_MAX_REPOS = 8
# Concurrent cold questions would each pay the scan; one builds, the rest wait.
# One lock per projection name, so a question waits only on a build of a
# projection it reads, never on another (such as the idle warmup's).
_LOCKS: dict[str, asyncio.Lock] = {}

# Sealed copies of the projections under the index's ``.repowise/mcp_cache/``,
# one file per repo and name (a workspace serves several repos). The seal's
# domain binds the code that built a value (package version + _STORE_SCHEMA),
# and the payload carries the index state it was built for, so a file left by
# another release or an older index fails to load and is rebuilt and
# overwritten. Two servers on one index may both build and write; each write
# is a whole-file replace of an equal value, so either one landing is correct.
# Ceiling: no eviction beyond that overwrite (about 50 MB on a 650 MB index), and
# a file whose repo left the workspace stays until the index directory goes;
# add a sweep here if projections ever grow per question rather than per index.
_STORE_DIRNAME = "mcp_cache"
# Bump when a persisted projection's build or pickled layout changes: a source
# checkout keeps one __version__ across commits, so only this number tells them
# apart there.
_STORE_SCHEMA = 1
# A temp file this old is an orphan of a killed write, never one in flight.
_ORPHAN_TEMP_SECONDS = 3600
_MISS = object()
# Writes in flight, held so the loop does not drop them before they finish.
_PENDING_STORES: set[asyncio.Task] = set()

# Confidence floor for ``calls`` edges. Imports are always 1.0; calls average
# 0.90 with a low-confidence tail from heuristic resolution. 0.5 keeps every
# genuine call and drops the noise.
CALLS_CONF_FLOOR = 0.5


def is_symbol_node(node_id: str) -> bool:
    """Whether *node_id* addresses a symbol rather than a file."""
    return "::" in node_id


# A projection asks once per edge end, over the same few thousand paths; bounded
# like the path classifier's memo so a long-lived server sees a fixed ceiling.
@lru_cache(maxsize=65536)
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
    _CACHE.clear()
    # A lock that once waited is bound to that event loop; a fresh one is not.
    _LOCKS.clear()


def _lock_for(name: str) -> asyncio.Lock:
    lock = _LOCKS.get(name)
    if lock is None:
        lock = _LOCKS[name] = asyncio.Lock()
    return lock


def _cached(repo_id: str, key: str, name: str) -> Any | None:
    entry = _CACHE.get(repo_id)
    if entry is None or entry[0] != key or name not in entry[1]:
        return None
    _CACHE.move_to_end(repo_id)
    return entry[1][name]


async def per_index(
    session: Any,
    repo_id: str,
    name: str,
    build: Callable[[], Awaitable[_T]],
    *,
    persist: bool = False,
) -> _T:
    """``await build()``, reused until this repo's index changes.

    With *persist*, a sealed copy is kept on disk for the next server process.
    Only for a value built from what an index or update run writes (pages,
    symbols, graph edges): those runs move the key. A value that also reads
    rows a health or dead-code run rewrites keeps the in-memory copy only,
    since those runs leave the key where it was and a restart is its refresh.

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
    async with _lock_for(name):
        hit = _cached(repo_id, key, name)
        if hit is not None:
            return hit
        stored_at = _stored_path(repository, repo_id, name) if persist else None
        value = _MISS
        if stored_at is not None:
            value = await asyncio.to_thread(_load_stored, stored_at, repo_id, name, key)
        if value is _MISS:
            value = await build()
            if stored_at is not None:
                _store_soon(stored_at, repo_id, name, key, value)
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


def _stored_path(repository: Any, repo_id: str, name: str) -> Path | None:
    """Where *name*'s sealed copy lives, or ``None`` when the index has no ``.repowise/``."""
    from repowise.server.mcp_server._meta import served_root

    root = served_root(repository) if repository is not None else None
    if not root:
        return None
    index_dir = Path(root) / ".repowise"
    if not index_dir.is_dir():
        return None
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", repo_id)
    return index_dir / _STORE_DIRNAME / f"{safe_id}.{name}.pkl"


def _store_domain(repo_id: str, name: str) -> str:
    from repowise.server import __version__

    return f"mcp-index-cache:{_STORE_SCHEMA}:{__version__}:{repo_id}:{name}"


def _load_stored(path: Path, repo_id: str, name: str, key: str) -> Any:
    """The value sealed at *path* for index state *key*, else ``_MISS``."""
    from repowise.core.cache_seal import load_sealed_pickle

    try:
        stored_key, value = load_sealed_pickle(path, domain=_store_domain(repo_id, name))
    except FileNotFoundError:
        return _MISS
    except Exception:
        # Unsigned, forged, truncated, from another release or layout, or
        # being replaced right now: build it again.
        _log.debug("repowise MCP: ignoring stored %s", path, exc_info=True)
        return _MISS
    return value if stored_key == key else _MISS


def _store(path: Path, repo_id: str, name: str, key: str, value: Any) -> None:
    from repowise.core.cache_seal import dump_sealed_pickle

    try:
        dump_sealed_pickle(path, (key, value), domain=_store_domain(repo_id, name))
    except Exception:
        # A read-only index, a file another server holds open (Windows refuses
        # the replace) or a value that cannot pickle only costs the next server
        # process a rebuild.
        _log.debug("repowise MCP: could not store %s", path, exc_info=True)
    _drop_orphan_temps(path)


def _drop_orphan_temps(path: Path) -> None:
    """Remove temp files a killed write left beside *path*."""
    cutoff = time.time() - _ORPHAN_TEMP_SECONDS
    for pattern in (f"{path.name}*.tmp", f"{path.name}*.pkltmp"):
        for orphan in path.parent.glob(pattern):
            with contextlib.suppress(OSError):
                if orphan.stat().st_mtime < cutoff:
                    orphan.unlink()


def _store_soon(path: Path, repo_id: str, name: str, key: str, value: Any) -> None:
    """Write the sealed copy on a worker thread; the caller does not wait for it."""
    task = asyncio.get_running_loop().create_task(
        asyncio.to_thread(_store, path, repo_id, name, key, value), name=f"store-{name}"
    )
    _PENDING_STORES.add(task)
    task.add_done_callback(_PENDING_STORES.discard)


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

    return await per_index(session, repo_id, "graph_file_paths", build, persist=True)
