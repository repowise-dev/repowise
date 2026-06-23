"""Structure-keyed disk cache for betweenness centrality.

Exact betweenness (Brandes) is the most expensive metric kernel on every
ingest — and an incremental ``repowise update`` recomputes it for the whole
graph even when the docstring-only edit it processed didn't change a single
edge. Betweenness is a pure function of the *structure* of the (unweighted)
subgraph it runs on, so values cache safely keyed by a signature over the
sorted node and edge sets: content edits that don't move call/heritage/import
edges hit; any structural change misses and recomputes.

One entry per kind (``file`` / ``symbol``) — the cache answers "is the graph
still the one I last scored?", not a history. Thread-safe because the init
pipeline computes both kinds concurrently. Best-effort by design: any
load/save error degrades to a fresh computation.

Note for the >30k-node sampled path (``nx.betweenness_centrality(k=...)``):
sampling is seedless, so recomputing the same graph yields slightly
different values run to run. A cache hit returns the previously sampled
values — deterministic where the status quo was not.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import pickle
import tempfile
import threading
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_CACHE_VERSION = 1
_CACHE_FILENAME = "centrality_cache.pkl"

__all__ = ["CentralityCache", "subgraph_signature"]


def subgraph_signature(graph) -> str:
    """Hash the structure (sorted nodes + edges) of *graph*.

    Attributes are deliberately excluded — betweenness on these subgraphs is
    unweighted, and the subgraph constructors already filtered edges by type.
    """
    h = hashlib.sha256()
    for node in sorted(graph.nodes()):
        h.update(node.encode("utf-8", "replace"))
        h.update(b"\x00")
    h.update(b"\x01")
    for u, v in sorted(graph.edges()):
        h.update(u.encode("utf-8", "replace"))
        h.update(b"\x00")
        h.update(v.encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()


class CentralityCache:
    """Pickle-backed ``kind -> (signature, values)`` store."""

    def __init__(self, cache_dir: Path | str) -> None:
        self._path = Path(cache_dir) / _CACHE_FILENAME
        self._entries: dict[str, tuple[str, dict[str, float]]] = {}
        self._lock = threading.Lock()
        self._loaded = False

    def __getstate__(self) -> dict:
        # A ``threading.Lock`` is not picklable, and the GraphBuilder that owns
        # this cache is pickled to hand graph state across a process boundary
        # (e.g. the hosted static-state bundle). Drop the lock here; the entries
        # are the only state worth carrying. ``__setstate__`` restores a fresh
        # lock.
        state = self.__dict__.copy()
        state["_lock"] = None
        return state

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            with self._path.open("rb") as fh:
                payload = pickle.load(fh)
            if payload.get("version") != _CACHE_VERSION:
                return
            self._entries = payload.get("entries", {})
        except FileNotFoundError:
            return
        except Exception as exc:  # corrupt / unreadable cache -> recompute
            log.debug("centrality_cache_load_failed", error=str(exc))

    def get(self, kind: str, signature: str) -> dict[str, float] | None:
        with self._lock:
            self._ensure_loaded()
            entry = self._entries.get(kind)
        if entry is None or entry[0] != signature:
            return None
        return dict(entry[1])

    def put(self, kind: str, signature: str, values: dict[str, float]) -> None:
        """Store *values* for *kind* and persist atomically."""
        with self._lock:
            self._ensure_loaded()
            self._entries[kind] = (signature, dict(values))
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                payload = {"version": _CACHE_VERSION, "entries": self._entries}
                fd, tmp_name = tempfile.mkstemp(
                    dir=str(self._path.parent), prefix=_CACHE_FILENAME, suffix=".tmp"
                )
                try:
                    with os.fdopen(fd, "wb") as fh:
                        pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
                    os.replace(tmp_name, self._path)
                except BaseException:
                    with contextlib.suppress(OSError):
                        os.unlink(tmp_name)
                    raise
            except Exception as exc:
                log.debug("centrality_cache_save_failed", error=str(exc))
