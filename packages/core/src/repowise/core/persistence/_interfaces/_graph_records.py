"""IndexStore mixin: relational graph rows, external systems, symbols.

The "graph records" surface of :class:`IndexStore` — anything that lives
in the SQL ``graph_nodes`` / ``graph_edges`` / ``external_systems`` /
``wiki_symbols`` tables. Read/write of the *in-memory* code graph
(NetworkX) is a separate contract: :class:`GraphStore`.

Split out from :class:`IndexStore` to keep each interface file under 400
lines.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import ExternalSystem, GraphEdge, GraphNode


class GraphRecordsIndexStore(ABC):
    """SQL CRUD for graph_nodes, graph_edges, external_systems, wiki_symbols."""

    # ------------------------------------------------------------------
    # graph_nodes / graph_edges
    # ------------------------------------------------------------------

    @abstractmethod
    async def batch_upsert_graph_nodes(
        self, repository_id: str, nodes: list[dict]
    ) -> None: ...

    @abstractmethod
    async def batch_upsert_graph_edges(
        self, repository_id: str, edges: list[dict]
    ) -> None: ...

    @abstractmethod
    async def get_graph_node(
        self, repository_id: str, node_id: str
    ) -> GraphNode | None: ...

    @abstractmethod
    async def get_graph_edges_for_node(
        self,
        repository_id: str,
        node_id: str,
        *,
        direction: str = "both",
        edge_types: list[str] | None = None,
        limit: int = 50,
    ) -> list[GraphEdge]: ...

    @abstractmethod
    async def get_graph_nodes_by_ids(
        self, repository_id: str, node_ids: list[str]
    ) -> dict[str, GraphNode]: ...

    @abstractmethod
    async def get_all_file_metrics(self, repository_id: str) -> list[GraphNode]: ...

    @abstractmethod
    async def get_community_members(
        self,
        repository_id: str,
        community_id: int,
        *,
        node_type: str = "file",
        limit: int = 50,
    ) -> list[GraphNode]: ...

    @abstractmethod
    async def get_cross_community_edges(
        self, repository_id: str, community_id: int
    ) -> list[dict]: ...

    @abstractmethod
    async def get_top_entry_points(
        self, repository_id: str, *, min_score: float = 0.3, limit: int = 20
    ) -> list[GraphNode]: ...

    @abstractmethod
    async def get_node_degree_counts(
        self, repository_id: str, node_id: str
    ) -> dict[str, int]: ...

    # ------------------------------------------------------------------
    # external_systems
    # ------------------------------------------------------------------

    @abstractmethod
    async def bulk_upsert_external_systems(
        self, repository_id: str, systems: list[dict]
    ) -> dict[tuple[str, str], int]: ...

    @abstractmethod
    async def link_graph_nodes_to_external_systems(
        self, repository_id: str, name_to_id: dict[str, int]
    ) -> int: ...

    @abstractmethod
    async def list_external_systems(
        self, repository_id: str
    ) -> list[ExternalSystem]: ...

    # ------------------------------------------------------------------
    # wiki_symbols
    # ------------------------------------------------------------------

    @abstractmethod
    async def batch_upsert_symbols(
        self, repository_id: str, symbols: list
    ) -> None: ...
