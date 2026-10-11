"""GraphStore — pluggable code-graph contract.

The in-tree default is an in-process NetworkX :class:`DiGraph` wrapped by
:class:`repowise.core.persistence.stores.in_process_graph_store.InProcessGraphStore`,
which delegates to the existing
:class:`repowise.core.ingestion.graph.GraphBuilder`.

The contract is deliberately small in this phase. It captures the
operations call-sites depend on today:

- mutating the graph one file/edge at a time during ingestion,
- finalising the build so cached metrics become valid,
- reading pagerank / betweenness / community membership for nodes,
- iterating neighbours for traversal-style analysis.

A future phase introduces a SQL-backed materialised view so the in-memory
graph can be dropped after build; that work will fit beneath this same
ABC by replacing the implementation, not the interface.

Plugin authors may back ``GraphStore`` with a hosted graph database
(Neo4j, FalkorDB, etc.) by honoring the method signatures here; the
contract test in ``tests/unit/persistence/test_interfaces_contract.py``
exercises the shared behavior.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class GraphStore(ABC):
    """Pluggable contract for the code dependency graph."""

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    @abstractmethod
    def add_node(self, node_id: str, **attrs: Any) -> None:
        """Add a node (or update its attributes if it already exists)."""

    @abstractmethod
    def add_edge(self, source: str, target: str, **attrs: Any) -> None:
        """Add a directed edge. Idempotent on (source, target) pairs."""

    @abstractmethod
    def has_node(self, node_id: str) -> bool: ...

    @abstractmethod
    def has_edge(self, source: str, target: str) -> bool: ...

    @abstractmethod
    def remove_node(self, node_id: str) -> None: ...

    @abstractmethod
    def remove_edge(self, source: str, target: str) -> None: ...

    # ------------------------------------------------------------------
    # Finalisation
    # ------------------------------------------------------------------

    @abstractmethod
    def build(self) -> None:
        """Mark the graph finalised. Implementations may compute cached
        metrics here (pagerank, communities) so subsequent read calls are
        fast. Calling :meth:`add_node` / :meth:`add_edge` after ``build``
        should invalidate caches."""

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    @abstractmethod
    def node_count(self) -> int: ...

    @abstractmethod
    def edge_count(self) -> int: ...

    @abstractmethod
    def get_node_attrs(self, node_id: str) -> dict[str, Any] | None:
        """Return the attribute dict for ``node_id`` or ``None`` if absent."""

    @abstractmethod
    def neighbors(self, node_id: str, *, direction: str = "out") -> list[str]:
        """Return neighbours of ``node_id``.

        ``direction``: ``"out"`` (successors), ``"in"`` (predecessors), or
        ``"both"`` (union). Implementations may apply repo-specific limits.
        """

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    @abstractmethod
    def pagerank(self) -> dict[str, float]:
        """Return ``{node_id: pagerank_score}`` for file-level nodes."""

    @abstractmethod
    def betweenness_centrality(self) -> dict[str, float]:
        """Return ``{node_id: betweenness_score}`` for file-level nodes."""

    @abstractmethod
    def communities(self) -> dict[str, int]:
        """Return ``{node_id: community_id}`` for file-level nodes."""
