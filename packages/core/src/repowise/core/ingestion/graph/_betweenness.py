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

import multiprocessing
import os
import threading
from concurrent.futures import ProcessPoolExecutor

import networkx as nx
import structlog

log = structlog.get_logger(__name__)

# ``spawn`` rather than the POSIX default ``fork``: graph metrics run after
# ingestion, and in a workspace init an earlier repo's doc generation may have
# already imported LanceDB (whose ``lance`` core is not fork-safe and installs
# an ``os.register_at_fork`` hook that can deadlock the child). Clean spawned
# workers never inherit that runtime. Same rationale as the parse pool in
# ``pipeline/phases/ingestion.py`` (issue #678).
_MP_SPAWN = multiprocessing.get_context("spawn")

# Upper bound on pool size, independent of how many cores the host has.
#
# Every worker is a fresh interpreter under ``spawn``, and on Windows they are
# brought up strictly one at a time: the parent runs CreateProcess, then
# pickles ``initargs`` and writes them down that child's pipe, and only then
# starts the next one. That write blocks until the brand-new child drains it,
# so the parent's per-spawn cost grows with the number of interpreters already
# competing for CPU and page-in bandwidth. A 32-thread host therefore paid 32
# serial spawns and 32 full copies of the edge list before the first source
# node was processed, and a single spawn that never got its reader scheduled
# stalled the whole update with no way to notice.
#
# Brandes parallelizes over source nodes and chunk count is derived from the
# worker count, so a lower cap means slightly larger chunks rather than idle
# cores; the measured win flattens out well before this many workers. Capping
# also makes the float summation order (fixed by chunk count) identical on
# every host with at least this many cores instead of varying per machine.
_MAX_POOL_WORKERS = 8

# Wall-clock budget for the entire parallel attempt: pool construction, the
# spawn of every worker, and the map. Exceeding it means the pool is not
# making progress, and the sequential path is taken instead.
#
# A hang here is not an exception, so the ``except`` below can never see it.
# The budget is what turns "no progress" into a fallback, which is the promise
# the module docstring already makes and previously only kept for failures
# that raised. Sized well above a healthy run (pool startup is seconds, and
# the sequential path this protects is itself a ~1 minute job) so a merely
# slow host is never cut off mid-computation.
_POOL_TIMEOUT_SECONDS = 600.0

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


def pool_worker_count(max_workers: int | None = None) -> int:
    """How many workers this module will ask a pool for.

    Separate from the caller's request so the bound is stated once and can be
    asserted on directly. An explicit ``max_workers`` is still capped: the
    limit exists to bound serial spawns on the host, which no caller is in a
    position to judge better than this module.
    """
    return min(max_workers or os.cpu_count() or 1, _MAX_POOL_WORKERS)


def _run_pool(
    *,
    n: int,
    edges: list[tuple[int, int]],
    directed: bool,
    chunks: list[list[int]],
    workers: int,
) -> list[float]:
    """Build the pool, run every chunk through it, and sum the partials."""
    totals = [0.0] * n
    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=_MP_SPAWN,
        initializer=_init_worker,
        initargs=(n, edges, directed),
    ) as pool:
        # ``map`` preserves chunk order, keeping float summation
        # deterministic for a given worker count.
        for partial in pool.map(_partial_betweenness, chunks):
            for v, b in partial.items():
                totals[v] += b
    return totals


def _run_pool_within_budget(
    *,
    n: int,
    edges: list[tuple[int, int]],
    directed: bool,
    chunks: list[list[int]],
    workers: int,
    timeout: float,
) -> list[float] | None:
    """Run the pool under a wall-clock budget. ``None`` means give up on it.

    The work runs on a daemon thread so that a spawn wedged inside a blocking
    pipe write cannot outlive the interpreter. There is no way to interrupt a
    thread parked in ``CreateProcess`` or ``reduction.dump`` from Python, so a
    timed-out attempt is abandoned rather than cancelled: the caller falls back
    to the sequential path and the stuck thread, plus any workers it managed to
    bring up, are left for process exit to reap. Leaking that is the lesser
    outcome against an update that never returns and holds its lock forever.
    """
    outcome: dict[str, object] = {}

    def _work() -> None:
        try:
            outcome["totals"] = _run_pool(
                n=n, edges=edges, directed=directed, chunks=chunks, workers=workers
            )
        except BaseException as exc:  # reported to the caller below
            outcome["error"] = exc

    worker = threading.Thread(target=_work, name="betweenness-pool", daemon=True)
    worker.start()
    worker.join(timeout)

    if worker.is_alive():
        log.warning(
            "betweenness_parallel_timed_out_falling_back",
            timeout_seconds=timeout,
            workers=workers,
        )
        return None
    error = outcome.get("error")
    if error is not None:
        log.warning("betweenness_parallel_failed_falling_back", error=str(error))
        return None
    totals = outcome.get("totals")
    return totals if isinstance(totals, list) else None


def betweenness_centrality_fast(
    g: nx.DiGraph,
    *,
    normalized: bool = True,
    max_workers: int | None = None,
    timeout: float | None = None,
) -> dict[str, float]:
    """Exact betweenness centrality, parallelized over sources when worthwhile.

    Drop-in equivalent of ``nx.betweenness_centrality(g, normalized=...)``
    for unweighted graphs without endpoint counting. Small graphs, any pool
    failure, and a pool that stops making progress within ``timeout`` all take
    the sequential NetworkX path, so callers never need a fallback of their own.
    """
    n = g.number_of_nodes()
    e = g.number_of_edges()
    if n == 0:
        return {}
    workers = pool_worker_count(max_workers)
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

    # Sorted, not graph order: the node numbering fixes the order the partial
    # sums are accumulated in, and float addition is not associative. Graph
    # insertion order varies between runs, so an unsorted numbering shifts
    # scores in the last ULP, which is enough to flip every downstream
    # tie-break that ranks on betweenness.
    nodes = sorted(g)
    index = {node: i for i, node in enumerate(nodes)}
    edges = [(index[u], index[v]) for u, v in g.edges()]

    # ~3 chunks per worker for load balancing without excessive IPC.
    chunk_count = max(1, workers * 3)
    chunk_size = max(1, (n + chunk_count - 1) // chunk_count)
    chunks = [list(range(i, min(i + chunk_size, n))) for i in range(0, n, chunk_size)]

    totals = _run_pool_within_budget(
        n=n,
        edges=edges,
        directed=g.is_directed(),
        chunks=chunks,
        workers=workers,
        timeout=_POOL_TIMEOUT_SECONDS if timeout is None else timeout,
    )
    if totals is None:
        return nx.betweenness_centrality(g, normalized=normalized)

    raw = dict(enumerate(totals))
    _rescale(raw, n, normalized=normalized, directed=g.is_directed())
    return {nodes[i]: score for i, score in raw.items()}
