"""InProcessGraphStore — default :class:`GraphStore` implementation.

Wraps a :class:`networkx.DiGraph` and computes metrics with NetworkX
algorithms. This matches what the ingestion pipeline does today via
:class:`repowise.core.ingestion.graph.GraphBuilder`; the GraphBuilder
continues to be the entry point for the file-aware build logic
(import resolution, heritage, calls), and an ``InProcessGraphStore``
can be constructed from a finished builder to expose its graph through
the pluggable contract::

    builder = GraphBuilder(repo_path)
    for parsed in parsed_files:
        builder.add_file(parsed)
    builder.build()
    store = InProcessGraphStore.from_graph(builder._graph)

A future phase replaces this with a SQL-backed materialised view so the
in-memory graph can be dropped after build; that work fits underneath
the same ABC by swapping the implementation.
"""

from __future__ import annotations

from typing import Any

import networkx as nx

from .._interfaces.graph_store import GraphStore


class InProcessGraphStore(GraphStore):
    """In-memory :class:`GraphStore` backed by a NetworkX DiGraph."""

    def __init__(self, graph: nx.DiGraph | None = None) -> None:
        self._graph: nx.DiGraph = graph if graph is not None else nx.DiGraph()
        self._built = False
        self._pagerank_cache: dict[str, float] | None = None
        self._betweenness_cache: dict[str, float] | None = None
        self._communities_cache: dict[str, int] | None = None

    @classmethod
    def from_graph(cls, graph: nx.DiGraph) -> InProcessGraphStore:
        """Adopt an existing DiGraph (e.g. one produced by ``GraphBuilder``)."""
        return cls(graph)

    @property
    def graph(self) -> nx.DiGraph:
        """Expose the underlying graph for callers that need NetworkX APIs."""
        return self._graph

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_node(self, node_id: str, **attrs: Any) -> None:
        self._graph.add_node(node_id, **attrs)
        self._invalidate()

    def add_edge(self, source: str, target: str, **attrs: Any) -> None:
        self._graph.add_edge(source, target, **attrs)
        self._invalidate()

    def has_node(self, node_id: str) -> bool:
        return node_id in self._graph

    def has_edge(self, source: str, target: str) -> bool:
        return self._graph.has_edge(source, target)

    def remove_node(self, node_id: str) -> None:
        if node_id in self._graph:
            self._graph.remove_node(node_id)
            self._invalidate()

    def remove_edge(self, source: str, target: str) -> None:
        if self._graph.has_edge(source, target):
            self._graph.remove_edge(source, target)
            self._invalidate()

    # ------------------------------------------------------------------
    # Finalisation
    # ------------------------------------------------------------------

    def build(self) -> None:
        self._built = True
        # Metric caches are filled lazily on first read.

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def node_count(self) -> int:
        return self._graph.number_of_nodes()

    def edge_count(self) -> int:
        return self._graph.number_of_edges()

    def get_node_attrs(self, node_id: str) -> dict[str, Any] | None:
        if node_id not in self._graph:
            return None
        return dict(self._graph.nodes[node_id])

    def neighbors(self, node_id: str, *, direction: str = "out") -> list[str]:
        if node_id not in self._graph:
            return []
        if direction == "out":
            return list(self._graph.successors(node_id))
        if direction == "in":
            return list(self._graph.predecessors(node_id))
        if direction == "both":
            return list(
                set(self._graph.successors(node_id))
                | set(self._graph.predecessors(node_id))
            )
        raise ValueError(f"direction must be 'out' | 'in' | 'both', got {direction!r}")

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def pagerank(self) -> dict[str, float]:
        if self._pagerank_cache is None:
            file_subgraph = self._file_subgraph()
            if file_subgraph.number_of_nodes() == 0:
                self._pagerank_cache = {}
            else:
                self._pagerank_cache = nx.pagerank(file_subgraph)
        return dict(self._pagerank_cache)

    def betweenness_centrality(self) -> dict[str, float]:
        if self._betweenness_cache is None:
            file_subgraph = self._file_subgraph()
            if file_subgraph.number_of_nodes() == 0:
                self._betweenness_cache = {}
            else:
                self._betweenness_cache = nx.betweenness_centrality(file_subgraph)
        return dict(self._betweenness_cache)

    def communities(self) -> dict[str, int]:
        if self._communities_cache is None:
            file_subgraph = self._file_subgraph().to_undirected()
            mapping: dict[str, int] = {}
            if file_subgraph.number_of_nodes():
                # Use NetworkX's greedy modularity communities — deterministic,
                # no external dependency, fine quality at this graph size.
                from networkx.algorithms.community import greedy_modularity_communities

                for cid, members in enumerate(
                    greedy_modularity_communities(file_subgraph)
                ):
                    for node in members:
                        mapping[node] = cid
            self._communities_cache = mapping
        return dict(self._communities_cache)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _invalidate(self) -> None:
        self._built = False
        self._pagerank_cache = None
        self._betweenness_cache = None
        self._communities_cache = None

    def _file_subgraph(self) -> nx.DiGraph:
        """Return the subgraph restricted to file-level nodes.

        Matches the convention used by :class:`GraphBuilder` — symbol
        nodes are excluded from file-centric metrics so PageRank reflects
        module-level importance rather than per-function frequency.
        """
        file_nodes = [
            n
            for n, attrs in self._graph.nodes(data=True)
            if attrs.get("node_type", "file") == "file"
        ]
        return self._graph.subgraph(file_nodes).copy()


__all__ = ["InProcessGraphStore"]
