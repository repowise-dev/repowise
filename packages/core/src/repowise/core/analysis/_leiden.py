"""Leiden partition through graspologic's native core.

``graspologic.partition.leiden`` is a thin wrapper over ``graspologic_native``,
but importing it runs ``graspologic/__init__``, which pulls in umap, pynndescent,
sklearn and numba (tens of seconds on every run that detects communities).
This module replays the wrapper's networkx path against the native call, so the
partition is the same for the same graph and seed.
"""

from __future__ import annotations

from typing import Any


def _native_edges(graph: Any) -> tuple[list[tuple[str, str, float]], dict[str, Any]]:
    """Edges as ``(str, str, float)`` plus the str-to-node map to undo the encoding."""
    originals: dict[str, Any] = {}

    def encode(node: Any) -> str:
        key = str(node)
        if originals.setdefault(key, node) != node:
            raise ValueError("str(value) results in a collision between distinct values")
        return key

    edges = [
        (encode(u), encode(v), float(w))
        for u, v, w in graph.edges(data="weight", default=1.0)
    ]
    return edges, originals


def leiden_partition(graph: Any, seed: int) -> dict[Any, int]:
    """``{node: community}`` for an undirected networkx graph; isolates are left out.

    Raises ImportError when ``graspologic_native`` is not installed.
    """
    import graspologic_native

    if graph.is_directed() or graph.is_multigraph():
        raise ValueError("Only undirected non-multi-graph networkx graphs are supported")
    edges, originals = _native_edges(graph)
    _quality, partition = graspologic_native.leiden(edges=edges, seed=seed)
    return {originals[key]: community for key, community in partition.items()}
