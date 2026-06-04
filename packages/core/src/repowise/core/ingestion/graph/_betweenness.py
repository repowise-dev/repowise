"""Parallel exact betweenness centrality (Brandes) over a process pool.

``nx.betweenness_centrality`` is pure-Python Brandes: O(V·E) per run, single
threaded, and it does not release the GIL — on a ~11k-node symbol subgraph it
is by far the most expensive metric kernel (~54s isolated, measured on the
repowise repo itself). Brandes is embarrassingly parallel over *source*
nodes: each source's dependency accumulation is independent and the final
score is the plain sum of the per-source partials. This module fans the
source set out across a process pool and sums the partials, producing the
same values as the sequential call (the only difference is floating-point
summation order, bounded at ~1e-15 relative).

Workers receive the graph once (pool initializer) as an integer edge list —
node attributes are irrelevant to betweenness, so the pickled payload stays
small even for graphs whose node ids are long symbol strings.

Falls back to ``nx.betweenness_centrality`` whenever the graph is small
enough that pool startup would dominate, or the pool / NetworkX internals
are unavailable.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor

import networkx as nx
import structlog

log = structlog.get_logger(__name__)

# Parallelize only when the estimated Brandes cost (~nodes x edges) is large
# enough to amortize process startup (~2-3s for pool spawn + imports).
# Measured rates on the repowise graph: file subgraph (1.8k x 5.2k ~ 1e7)
# runs in ~0.4s sequentially; the symbol subgraph (11.3k x 12k ~ 1.4e8)
# takes ~54s. The threshold sits between the two.
_PARALLEL_COST_THRESHOLD = 4e7

# ---------------------------------------------------------------------------
# Worker side (must be module-level + picklable for Windows spawn)
# ---------------------------------------------------------------------------

_WORKER_GRAPH: nx.DiGraph | None = None


def _init_worker(n: int, edges: list[tuple[int, int]], directed: bool) -> None:
    """Pool initializer: build the (attribute-free) int-labeled graph once."""
    global _WORKER_GRAPH
    g: nx.DiGraph = nx.DiGraph() if directed else nx.Graph()
    g.add_nodes_from(range(n))
    g.add_edges_from(edges)
    _WORKER_GRAPH = g


def _partial_betweenness(sources: list[int]) -> dict[int, float]:
    """Accumulate Brandes dependencies for a chunk of source nodes.

    Mirrors the per-source loop of ``nx.betweenness_centrality`` exactly
    (unweighted BFS + basic accumulation, no endpoints). Summing the
    partials over all chunks reproduces the sequential pre-rescale values.
    """
    from networkx.algorithms.centrality.betweenness import (
        _accumulate_basic,
        _single_source_shortest_path_basic,
    )

    g = _WORKER_GRAPH
    assert g is not None, "worker pool initializer did not run"
    betweenness: dict[int, float] = dict.fromkeys(g, 0.0)
    for s in sources:
        S, P, sigma, _ = _single_source_shortest_path_basic(g, s)  # noqa: N806 - NX naming
        betweenness, _ = _accumulate_basic(betweenness, S, P, sigma, s)
    # Only non-zero entries cross the process boundary.
    return {v: b for v, b in betweenness.items() if b != 0.0}


# ---------------------------------------------------------------------------
# Parent side
# ---------------------------------------------------------------------------


def _rescale(
    betweenness: dict[int, float], n: int, *, normalized: bool, directed: bool
) -> dict[int, float]:
    """Rescale raw accumulations exactly like NetworkX (k=None, no endpoints)."""
    if normalized:
        scale = None if n <= 2 else 1 / ((n - 1) * (n - 2))
    else:
        scale = None if directed else 0.5
    if scale is not None:
        for v in betweenness:
            betweenness[v] *= scale
    return betweenness


def betweenness_centrality_fast(
    g: nx.DiGraph,
    *,
    normalized: bool = True,
    max_workers: int | None = None,
) -> dict[str, float]:
    """Exact betweenness centrality, parallelized over sources when worthwhile.

    Drop-in equivalent of ``nx.betweenness_centrality(g, normalized=...)``
    for unweighted graphs without endpoint counting. Small graphs (or any
    pool failure) take the sequential NetworkX path, so callers never need
    a fallback of their own.
    """
    n = g.number_of_nodes()
    e = g.number_of_edges()
    if n == 0:
        return {}
    workers = max_workers or os.cpu_count() or 1
    if n * e < _PARALLEL_COST_THRESHOLD or workers < 2:
        return nx.betweenness_centrality(g, normalized=normalized)

    try:
        # Verify the NetworkX internals the workers rely on exist in this
        # version before paying for pool spawn.
        from networkx.algorithms.centrality.betweenness import (  # noqa: F401
            _accumulate_basic,
            _single_source_shortest_path_basic,
        )
    except ImportError:  # pragma: no cover - depends on networkx version
        log.warning("betweenness_parallel_unavailable", reason="networkx internals moved")
        return nx.betweenness_centrality(g, normalized=normalized)

    nodes = list(g)
    index = {node: i for i, node in enumerate(nodes)}
    edges = [(index[u], index[v]) for u, v in g.edges()]

    # ~3 chunks per worker for load balancing without excessive IPC.
    chunk_count = max(1, workers * 3)
    chunk_size = max(1, (n + chunk_count - 1) // chunk_count)
    chunks = [list(range(i, min(i + chunk_size, n))) for i in range(0, n, chunk_size)]

    totals = [0.0] * n
    try:
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_worker,
            initargs=(n, edges, g.is_directed()),
        ) as pool:
            # ``map`` preserves chunk order, keeping float summation
            # deterministic for a given worker count.
            for partial in pool.map(_partial_betweenness, chunks):
                for v, b in partial.items():
                    totals[v] += b
    except Exception as exc:  # pragma: no cover - depends on host environment
        log.warning("betweenness_parallel_failed_falling_back", error=str(exc))
        return nx.betweenness_centrality(g, normalized=normalized)

    raw = dict(enumerate(totals))
    _rescale(raw, n, normalized=normalized, directed=g.is_directed())
    return {nodes[i]: score for i, score in raw.items()}
