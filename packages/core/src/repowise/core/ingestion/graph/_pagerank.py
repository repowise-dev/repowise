"""PageRank over a metric subgraph, reused from the centrality cache when the structure is unchanged."""

from __future__ import annotations

from typing import Any

import networkx as nx
import structlog

log = structlog.get_logger(__name__)


def _compute(g: nx.DiGraph, alpha: float, kind: str) -> dict[str, float]:
    try:
        return nx.pagerank(g, alpha=alpha)
    except nx.PowerIterationFailedConvergence:
        log.warning("pagerank_did_not_converge_using_uniform_scores", kind=kind)
        n = g.number_of_nodes()
        return {node: 1.0 / n for node in g.nodes()}


def _is_unweighted(g: nx.DiGraph) -> bool:
    # The signature hashes structure only, but nx.pagerank reads edge weights.
    # Every metric edge weighs 1 today; a graph that breaks that is not cached.
    return all(d.get("weight", 1) == 1 for _, _, d in g.edges(data=True))


def cached_pagerank(
    g: nx.DiGraph, *, kind: str, alpha: float, cache: Any | None
) -> dict[str, float]:
    """PageRank of *g*, served from *cache* when *kind* was last scored on this exact structure.

    A body-only edit leaves every metric subgraph unchanged, so the update
    reruns nothing. Cache errors degrade to a fresh computation.
    """
    if g.number_of_nodes() == 0:
        return {}
    if not _is_unweighted(g):
        cache = None
    signature: str | None = None
    if cache is not None:
        try:
            signature = cache.signature(kind, g)
            hit = cache.lookup_pagerank(kind, signature=signature, alpha=alpha)
            if hit is not None:
                log.debug("pagerank_reused_from_cache", kind=kind, nodes=len(hit))
                return hit
        except Exception as exc:
            log.warning("pagerank_cache_lookup_failed", kind=kind, error=str(exc))
            signature = None

    values = _compute(g, alpha, kind)
    if cache is not None and signature is not None:
        try:
            cache.put_pagerank(kind, signature=signature, alpha=alpha, values=values)
        except Exception as exc:
            log.warning("pagerank_cache_store_failed", kind=kind, error=str(exc))
    return values
