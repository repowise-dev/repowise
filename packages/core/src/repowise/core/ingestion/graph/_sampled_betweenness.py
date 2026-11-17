"""Seeded k-sample betweenness over the betweenness process pool, bit-identical to NetworkX.

Above the large-graph threshold betweenness is estimated from ``k`` sampled
sources, and the sequential NetworkX loop over them takes minutes. The sources
are independent, so they fan out over the same pool as the exact kernel.

The exact kernel tolerates a different float summation order; this one does
not have to. Each worker returns every sampled source's dependencies unsummed,
and the parent adds them in sample order, which is the order NetworkX adds
them in. Worker graphs keep the node numbering and edge order of the graph
NetworkX would have scored, so each BFS visits in the same order too. The
result equals the sequential call bit for bit.
"""

from __future__ import annotations

import inspect
import random
from array import array

import networkx as nx

from . import _betweenness

# Two sources per chunk at k=500 on 8 workers. Unsummed results scale with the
# nodes each source reaches, so small chunks bound what waits for the in-order
# fold (~32 sources, ~150 MB on a fully reachable 400k-node graph).
_CHUNKS_PER_WORKER = 32


def _source_dependencies(sources: list[int]) -> list[tuple[array, array]]:
    """Each source's dependencies, unsummed, as ``(nodes, deltas)``; zeros left out."""
    from networkx.algorithms.centrality.betweenness import (
        _accumulate_basic,
        _single_source_shortest_path_basic,
    )

    g = _betweenness._WORKER_GRAPH
    assert g is not None, "worker pool initializer did not run"
    out: list[tuple[array, array]] = []
    for s in sources:
        stack, preds, sigma, _ = _single_source_shortest_path_basic(g, s)
        # A fresh zero per reached node: 0.0 + delta is delta exactly, so this
        # is NetworkX's own accumulation, kept per source.
        own = dict.fromkeys(stack, 0.0)
        own, _ = _accumulate_basic(own, stack, preds, sigma, s)
        nodes, deltas = array("i"), array("d")
        for v, d in own.items():
            if d:
                nodes.append(v)
                deltas.append(d)
        out.append((nodes, deltas))
    return out


def _add_in_order(totals: list[float], per_source: list[tuple[array, array]]) -> None:
    for nodes, deltas in per_source:
        for v, d in zip(nodes, deltas, strict=True):
            totals[v] += d


def _sequential(g: nx.DiGraph, nodes: list, k: int, seed: int) -> dict[str, float]:
    # NetworkX samples from ``list(G.nodes())``, so a fixed seed over a
    # population in a different order still draws different nodes. Re-inserting
    # the nodes in sorted order is what makes the sample reproducible.
    ordered = nx.DiGraph() if g.is_directed() else nx.Graph()
    ordered.add_nodes_from(nodes)
    ordered.add_edges_from(g.edges())
    return nx.betweenness_centrality(ordered, k=k, normalized=True, seed=seed)


def _rescale_supports_sampling() -> bool:
    from networkx.algorithms.centrality.betweenness import _rescale

    return "sampled_nodes" in inspect.signature(_rescale).parameters


def sampled_betweenness_centrality(
    g: nx.DiGraph,
    *,
    k: int,
    seed: int,
    max_workers: int | None = None,
    timeout: float | None = None,
) -> dict[str, float]:
    """Normalized betweenness estimated from *k* sources drawn with *seed*.

    Equal to ``nx.betweenness_centrality`` over *g* re-inserted in sorted node
    order. Cheap graphs, NetworkX versions whose internals differ, and any pool
    failure or stall take that sequential call directly.
    """
    nodes, edges = _betweenness.int_graph(g)
    n = len(nodes)
    workers = _betweenness.pool_worker_count(max_workers)
    cost = k * len(edges)
    if k >= n or cost < _betweenness._PARALLEL_COST_THRESHOLD or workers < 2:
        return _sequential(g, nodes, k, seed)
    if not _rescale_supports_sampling():  # older networkx
        return _sequential(g, nodes, k, seed)

    # The draw NetworkX makes: random.Random(seed) over the sorted population.
    index = {node: i for i, node in enumerate(nodes)}
    sample = random.Random(seed).sample(nodes, k)
    sources = [index[node] for node in sample]
    size = max(1, -(-k // (workers * _CHUNKS_PER_WORKER)))
    chunks = [sources[i : i + size] for i in range(0, k, size)]

    totals = _betweenness._run_pool_within_budget(
        n=n,
        edges=edges,
        directed=g.is_directed(),
        chunks=chunks,
        workers=workers,
        timeout=_betweenness._POOL_TIMEOUT_SECONDS if timeout is None else timeout,
        task=_source_dependencies,
        fold=_add_in_order,
    )
    if totals is None:
        return _sequential(g, nodes, k, seed)

    from networkx.algorithms.centrality.betweenness import _rescale

    raw = dict(zip(nodes, totals, strict=True))
    return _rescale(
        raw, n, normalized=True, directed=g.is_directed(), endpoints=False, sampled_nodes=sample
    )
