"""Structure-keyed disk cache for betweenness centrality.

Exact betweenness (Brandes) is the most expensive metric kernel on every
ingest — and an incremental ``repowise update`` recomputes it for the whole
graph even when the docstring-only edit it processed didn't change a single
edge. Betweenness is a pure function of the *structure* of the (unweighted)
subgraph it runs on, so values cache safely keyed by a signature over the
sorted node and edge sets: content edits that don't move call/heritage/import
edges hit exactly.

A structural change used to mean a full recompute, which made "add one
function" the most expensive shape of edit there is — an order of magnitude
dearer than a body-only one. It barely moves the answer: on a 13k-symbol graph
one added function left every surviving rank within four places. So
:meth:`CentralityCache.lookup` serves the last exact scoring while the graph
has drifted no further from it than ``max_churn`` nodes-plus-edges, and reports
how far that is.

The reference set stays pinned to the last *exact* scoring rather than
advancing on each reuse, so churn accumulates monotonically and the staleness
window cannot be walked past one edit at a time.

One entry per kind (``file`` / ``symbol``) — the cache answers "how far is the
graph from the one I last scored?", not a history. Thread-safe because the init
pipeline computes both kinds concurrently. Best-effort by design: any
load/save error degrades to a fresh computation.

Note for the >30k-node sampled path (``nx.betweenness_centrality(k=...)``):
sampling is seedless, so recomputing the same graph yields slightly
different values run to run. A cache hit returns the previously sampled
values — deterministic where the status quo was not.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path

import structlog

from repowise.core.cache_seal import dump_sealed_pickle, load_sealed_pickle

log = structlog.get_logger(__name__)

# 2 when betweenness was made order-independent: the signature keys on graph
# structure only, so a warm cache would keep serving the old, insertion-order-
# dependent values and two installs at the same commit would still disagree.
# 3 when entries gained the scored edge set and commit, needed to answer "how
# far has the graph drifted, and which nodes were never in this scoring".
_CACHE_VERSION = 3
_CACHE_FILENAME = "centrality_cache.pkl"

__all__ = ["BetweennessScoring", "CentralityCache", "subgraph_signature"]


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


def _edge_fingerprints(graph) -> frozenset[int]:
    """Fingerprint each edge, so churn can be *counted* and not just detected.

    The signature answers "identical?"; measuring drift needs a set difference.
    Fingerprints rather than the ``(u, v)`` pairs because this is persisted per
    repository and each pair is two full symbol paths.
    """
    return frozenset(
        int.from_bytes(
            hashlib.blake2b(f"{u}\x00{v}".encode(errors="replace"), digest_size=8).digest(),
            "big",
        )
        for u, v in graph.edges()
    )


@dataclass(frozen=True)
class _Entry:
    """One kind's last exact scoring, and the structure it was scored on."""

    signature: str
    values: dict[str, float]
    edges: frozenset[int]
    scored_commit: str | None


@dataclass(frozen=True)
class BetweennessScoring:
    """Cached values, plus what a caller needs to judge their age.

    ``churn`` is how many nodes and edges the graph now differs by from the one
    these were computed on — 0 for an exact hit. Nodes absent from ``values``
    appeared since and have never been scored; callers must say so rather than
    let them read as a legitimate 0.0.
    """

    values: dict[str, float]
    scored_commit: str | None
    churn: int


class CentralityCache:
    """Pickle-backed ``kind -> last exact scoring`` store."""

    def __init__(self, cache_dir: Path | str) -> None:
        self._path = Path(cache_dir) / _CACHE_FILENAME
        self._entries: dict[str, _Entry] = {}
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
            payload = load_sealed_pickle(self._path, domain=_CACHE_FILENAME)
            if payload.get("version") != _CACHE_VERSION:
                return
            self._entries = payload.get("entries", {})
        except FileNotFoundError:
            return
        except Exception as exc:  # corrupt / unsigned / unreadable -> recompute
            log.debug("centrality_cache_load_failed", error=str(exc))

    def lookup(
        self, kind: str, graph, *, signature: str, max_churn: int
    ) -> BetweennessScoring | None:
        """Return the last exact scoring for *kind* if *graph* is close enough.

        ``None`` when there is no entry, or when the graph has drifted further
        than *max_churn* nodes-plus-edges from the one that was scored; both
        mean the caller must recompute.
        """
        with self._lock:
            self._ensure_loaded()
            entry = self._entries.get(kind)
        if entry is None:
            return None
        if entry.signature == signature:
            return BetweennessScoring(dict(entry.values), entry.scored_commit, 0)
        # Both compute paths return one entry per node, so the scored node set
        # is the values' keys.
        nodes = set(graph.nodes())
        scored = entry.values.keys()
        churn = len(nodes - scored) + len(scored - nodes)
        churn += len(_edge_fingerprints(graph) ^ entry.edges)
        if churn > max_churn:
            return None
        return BetweennessScoring(dict(entry.values), entry.scored_commit, churn)

    def put(
        self,
        kind: str,
        signature: str,
        values: dict[str, float],
        *,
        graph,
        scored_commit: str | None,
    ) -> None:
        """Record an exact scoring for *kind* and persist atomically."""
        with self._lock:
            self._ensure_loaded()
            self._entries[kind] = _Entry(
                signature, dict(values), _edge_fingerprints(graph), scored_commit
            )
            try:
                payload = {"version": _CACHE_VERSION, "entries": self._entries}
                dump_sealed_pickle(self._path, payload, domain=_CACHE_FILENAME)
            except Exception as exc:
                log.debug("centrality_cache_save_failed", error=str(exc))
