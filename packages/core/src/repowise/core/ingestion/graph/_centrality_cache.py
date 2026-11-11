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

PageRank rides in the same file, reused only on an exact signature match (and
only for unweighted graphs, see ``cached_pagerank``). A reused value is the one
the previous run computed and persisted; a fresh one would normally differ only
in the last bits, because summation follows insertion order.

Note for the >30k-node sampled path (``nx.betweenness_centrality(k=...)``):
sampling is seedless, so recomputing the same graph yields slightly
different values run to run. A cache hit returns the previously sampled
values — deterministic where the status quo was not.
"""

from __future__ import annotations

import hashlib
import threading
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from repowise.core.cache_seal import dump_sealed_pickle, load_sealed_pickle

log = structlog.get_logger(__name__)

# 2 when betweenness was made order-independent: the signature keys on graph
# structure only, so a warm cache would keep serving the old, insertion-order-
# dependent values and two installs at the same commit would still disagree.
# 3 when entries gained the scored edge set and commit, needed to answer "how
# far has the graph drifted, and which nodes were never in this scoring".
# 4 when file betweenness moved to the graph without test-to-test edges; a
# churn-budget hit would otherwise keep serving the old scoring.
# 5 when community detection results (CommunityInfo) and execution flow reports
# were added to the cache, with flow reports keyed by symbol subgraph signature.
# 6 when file and symbol communities were keyed by exact subgraph signature,
# and execution flows were keyed on a comprehensive execution-graph signature.
_CACHE_VERSION = 6
_CACHE_FILENAME = "centrality_cache.pkl"

__all__ = [
    "BetweennessScoring",
    "CentralityCache",
    "execution_flow_signature",
    "file_community_signature",
    "subgraph_signature",
    "symbol_community_signature",
]


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


def file_community_signature(graph) -> str:
    """Hash the structure and attributes that file community detection reads."""
    from repowise.core.analysis.communities import (
        _FILE_COMMUNITY_EDGE_TYPES,
        _is_example_node,
        _is_test_node,
    )
    from repowise.core.ids import is_external

    h = hashlib.sha256()
    file_nodes = sorted(
        n
        for n, d in graph.nodes(data=True)
        if d.get("node_type", "file") == "file" and not is_external(n) and not d.get("is_external", False)
    )
    for n in file_nodes:
        h.update(n.encode("utf-8", "replace"))
        h.update(b"\x00")
        d = graph.nodes[n]
        is_test = _is_test_node(n, d)
        is_example = _is_example_node(n, d)
        h.update(b"T" if is_test else b"P")
        h.update(b"E" if is_example else b"O")
    h.update(b"\x01")
    file_set = frozenset(file_nodes)
    edges = sorted(
        (u, v)
        for u, v, d in graph.edges(data=True)
        if d.get("edge_type", "imports") in _FILE_COMMUNITY_EDGE_TYPES
        and u in file_set
        and v in file_set
    )
    for u, v in edges:
        h.update(u.encode("utf-8", "replace"))
        h.update(b"\x00")
        h.update(v.encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()


def symbol_community_signature(graph) -> str:
    """Hash the symbol nodes and call/heritage edges that symbol community detection reads."""
    from repowise.core.analysis.communities import _SYMBOL_COMMUNITY_EDGE_TYPES

    h = hashlib.sha256()
    symbol_nodes = sorted(
        n for n, d in graph.nodes(data=True) if d.get("node_type") == "symbol"
    )
    for n in symbol_nodes:
        h.update(n.encode("utf-8", "replace"))
        h.update(b"\x00")
    h.update(b"\x01")
    symbol_set = frozenset(symbol_nodes)
    edges = sorted(
        (u, v)
        for u, v, d in graph.edges(data=True)
        if d.get("edge_type") in _SYMBOL_COMMUNITY_EDGE_TYPES
        and u in symbol_set
        and v in symbol_set
    )
    for u, v in edges:
        h.update(u.encode("utf-8", "replace"))
        h.update(b"\x00")
        h.update(v.encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()


def execution_flow_signature(graph, community_sig: str = "") -> str:
    """Hash the nodes, attributes, and walkable edges that execution flow tracing reads."""
    h = hashlib.sha256()
    h.update(community_sig.encode("utf-8"))
    h.update(b"\xff")
    for n, d in sorted(graph.nodes(data=True)):
        nt = d.get("node_type", "file")
        if nt == "file":
            if d.get("is_entry_point"):
                h.update(n.encode("utf-8", "replace"))
                h.update(b":entry\x00")
        elif nt == "symbol":
            h.update(n.encode("utf-8", "replace"))
            h.update(b"\x00")
            vis = str(d.get("visibility", "public"))
            name = str(d.get("name", ""))
            h.update(vis.encode("utf-8", "replace"))
            h.update(b"\x00")
            h.update(name.encode("utf-8", "replace"))
            h.update(b"\x00")
    h.update(b"\x01")
    for u, v, d in sorted(graph.edges(data=True)):
        etype = str(d.get("edge_type", ""))
        origin = str(d.get("resolution_origin", ""))
        conf = str(d.get("confidence", ""))
        h.update(u.encode("utf-8", "replace"))
        h.update(b"\x00")
        h.update(v.encode("utf-8", "replace"))
        h.update(b"\x00")
        h.update(etype.encode("utf-8", "replace"))
        h.update(b"\x00")
        h.update(origin.encode("utf-8", "replace"))
        h.update(b"\x00")
        h.update(conf.encode("utf-8", "replace"))
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
        # kind -> (signature, alpha, values). Exact-signature reuse only: unlike
        # betweenness, PageRank is cheap enough that drift is never worth serving.
        self._pagerank: dict[str, tuple[str, float, dict[str, float]]] = {}
        self._file_communities: tuple[str, dict[str, int], dict[int, Any], str] | None = None
        self._symbol_communities: tuple[str, dict[str, int]] | None = None
        self._execution_flow: tuple[str, Any] | None = None
        self._lock = threading.Lock()
        self._loaded = False
        self._signatures: dict[str, tuple[weakref.ref, str]] = {}
        self._signature_lock = threading.Lock()

    def __getstate__(self) -> dict:
        # A ``threading.Lock`` is not picklable, and the GraphBuilder that owns
        # this cache is pickled to hand graph state across a process boundary
        # (e.g. the hosted static-state bundle). Drop the lock here; the entries
        # are the only state worth carrying. ``__setstate__`` restores a fresh
        # lock.
        state = self.__dict__.copy()
        state["_lock"] = None
        state["_signature_lock"] = None
        state["_signatures"] = {}  # weak references do not pickle
        return state

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self._lock = threading.Lock()
        self._signature_lock = threading.Lock()
        self.__dict__.setdefault("_signatures", {})
        self.__dict__.setdefault("_pagerank", {})
        self.__dict__.setdefault("_file_communities", None)
        self.__dict__.setdefault("_symbol_communities", None)
        self.__dict__.setdefault("_execution_flow", None)

    def signature(self, kind: str, graph) -> str:
        """:func:`subgraph_signature` of *graph*, hashed once per subgraph object.

        PageRank and betweenness of a kind run on the same subgraph, often on
        concurrent threads; the lock makes the second caller wait for the first
        hash instead of repeating it. Weakly held so a released subgraph frees.
        """
        with self._signature_lock:
            memo = self._signatures.get(kind)
            if memo is not None and memo[0]() is graph:
                return memo[1]
            signature = subgraph_signature(graph)
            self._signatures[kind] = (weakref.ref(graph), signature)
            return signature

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            payload = load_sealed_pickle(self._path, domain=_CACHE_FILENAME)
            if payload.get("version") != _CACHE_VERSION:
                return
            self._entries = payload.get("entries", {})
            # Optional key: files written before PageRank was cached lack it.
            self._pagerank = payload.get("pagerank", {})
            fc = payload.get("file_communities")
            if isinstance(fc, tuple) and len(fc) == 4:
                self._file_communities = fc
            sc = payload.get("symbol_communities")
            if isinstance(sc, tuple) and len(sc) == 2:
                self._symbol_communities = sc
            ef = payload.get("execution_flow")
            if isinstance(ef, tuple) and len(ef) == 2:
                self._execution_flow = ef
        except FileNotFoundError:
            return
        except Exception as exc:  # corrupt / unsigned / unreadable -> recompute
            log.debug("centrality_cache_load_failed", error=str(exc))

    def _save_locked(self) -> None:
        """Persist every entry atomically; caller holds ``_lock``."""
        try:
            payload = {
                "version": _CACHE_VERSION,
                "entries": self._entries,
                "pagerank": self._pagerank,
                "file_communities": self._file_communities,
                "symbol_communities": self._symbol_communities,
                "execution_flow": self._execution_flow,
            }
            dump_sealed_pickle(self._path, payload, domain=_CACHE_FILENAME)
        except Exception as exc:
            log.debug("centrality_cache_save_failed", error=str(exc))

    def lookup_pagerank(
        self, kind: str, *, signature: str, alpha: float
    ) -> dict[str, float] | None:
        """Return *kind*'s stored PageRank if it was computed on this exact structure."""
        with self._lock:
            self._ensure_loaded()
            entry = self._pagerank.get(kind)
        if entry is None or entry[0] != signature or entry[1] != alpha:
            return None
        return dict(entry[2])

    def put_pagerank(
        self, kind: str, *, signature: str, alpha: float, values: dict[str, float]
    ) -> None:
        """Record *kind*'s PageRank for this structure and persist atomically."""
        with self._lock:
            self._ensure_loaded()
            self._pagerank[kind] = (signature, alpha, dict(values))
            self._save_locked()

    def lookup_file_communities(
        self, *, signature: str, rescore_due: bool = True
    ) -> tuple[dict[str, int], dict[int, Any], str] | None:
        """Return cached (assignment, info, algo) for file-level communities if signature matches."""
        if rescore_due:
            return None
        with self._lock:
            self._ensure_loaded()
            if self._file_communities is None:
                return None
            cached_sig, assignment, info, algo = self._file_communities
            if cached_sig != signature:
                return None
            import copy

            return dict(assignment), copy.deepcopy(info), algo

    def put_file_communities(
        self, signature: str, assignment: dict[str, int], info: dict[int, Any], algo: str
    ) -> None:
        """Record file-level community detection results for signature and persist atomically."""
        import copy

        with self._lock:
            self._ensure_loaded()
            self._file_communities = (signature, dict(assignment), copy.deepcopy(info), algo)
            self._save_locked()

    def lookup_symbol_communities(
        self, *, signature: str, rescore_due: bool = True
    ) -> dict[str, int] | None:
        """Return cached symbol-level community assignment if signature matches."""
        if rescore_due:
            return None
        with self._lock:
            self._ensure_loaded()
            if self._symbol_communities is None:
                return None
            cached_sig, assignment = self._symbol_communities
            if cached_sig != signature:
                return None
            return dict(assignment)

    def put_symbol_communities(self, signature: str, assignment: dict[str, int]) -> None:
        """Record symbol-level community detection results for signature and persist atomically."""
        with self._lock:
            self._ensure_loaded()
            self._symbol_communities = (signature, dict(assignment))
            self._save_locked()

    def lookup_execution_flows(
        self, *, signature: str, rescore_due: bool = True
    ) -> Any | None:
        """Return cached execution flow report if computed on this exact symbol structure."""
        if rescore_due:
            return None
        with self._lock:
            self._ensure_loaded()
            if self._execution_flow is None:
                return None
            cached_sig, report = self._execution_flow
            if cached_sig != signature:
                return None
            return report

    def put_execution_flows(self, signature: str, report: Any) -> None:
        """Record execution flow report for *signature* and persist atomically."""
        with self._lock:
            self._ensure_loaded()
            self._execution_flow = (signature, report)
            self._save_locked()

    def lookup(
        self,
        kind: str,
        graph,
        *,
        signature: str,
        max_churn: int,
        rescore_due: bool = True,
    ) -> BetweennessScoring | None:
        """Return the last exact scoring for *kind* if *graph* is close enough.

        ``None`` when there is no entry, or when the graph has drifted further
        than *max_churn* nodes-plus-edges from the one that was scored and
        *rescore_due* is True; both mean the caller must recompute. When
        *rescore_due* is False (e.g. during incremental update between periodic
        cadences), the previous scoring continues to be served past the churn budget
        as long as the graph still overlaps with scored nodes.
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
        scored = set(entry.values.keys())
        if not (nodes & scored):
            return None
        churn = len(nodes - scored) + len(scored - nodes)
        churn += len(_edge_fingerprints(graph) ^ entry.edges)
        # Zero churn budget means the graph is small enough that recompute is
        # sub-second; only exact-signature matches are reused, even if rescore is not due.
        if max_churn == 0 and entry.signature != signature:
            return None
        if rescore_due and churn > max_churn:
            return None
        # Bound drift during incremental updates: beyond a multiple of the
        # churn budget, even an unscheduled run must rescore rather than serve
        # a grossly drifted ranking (e.g. following a large directory rename).
        if not rescore_due and churn > max(10, 4 * max_churn):
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
            self._save_locked()
