"""Graph-records delegations for :class:`SqlIndexStore`.

graph_nodes / graph_edges / external_systems / wiki_symbols — each method
delegates to :mod:`crud`. Split out to keep store files under 400 lines.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from .. import crud
from .._interfaces._graph_records import GraphRecordsIndexStore
from ..models import ExternalSystem, GraphEdge, GraphNode


class _SqlGraphRecordsMixin(GraphRecordsIndexStore):
    """Concrete delegations for the graph-records IndexStore surface."""

    _session: AsyncSession

    async def batch_upsert_graph_nodes(
        self, repository_id: str, nodes: list[dict]
    ) -> None:
        await crud.batch_upsert_graph_nodes(self._session, repository_id, nodes)

    async def batch_upsert_graph_edges(
        self, repository_id: str, edges: list[dict]
    ) -> None:
        await crud.batch_upsert_graph_edges(self._session, repository_id, edges)

    async def get_graph_node(
        self, repository_id: str, node_id: str
    ) -> GraphNode | None:
        return await crud.get_graph_node(self._session, repository_id, node_id)

    async def get_graph_edges_for_node(
        self,
        repository_id: str,
        node_id: str,
        *,
        direction: str = "both",
        edge_types: list[str] | None = None,
        limit: int = 50,
    ) -> list[GraphEdge]:
        return await crud.get_graph_edges_for_node(
            self._session,
            repository_id,
            node_id,
            direction=direction,
            edge_types=edge_types,
            limit=limit,
        )

    async def get_graph_nodes_by_ids(
        self, repository_id: str, node_ids: list[str]
    ) -> dict[str, GraphNode]:
        return await crud.get_graph_nodes_by_ids(
            self._session, repository_id, node_ids
        )

    async def get_all_file_metrics(self, repository_id: str) -> list[GraphNode]:
        return await crud.get_all_file_metrics(self._session, repository_id)

    async def get_community_members(
        self,
        repository_id: str,
        community_id: int,
        *,
        node_type: str = "file",
        limit: int = 50,
    ) -> list[GraphNode]:
        return await crud.get_community_members(
            self._session,
            repository_id,
            community_id,
            node_type=node_type,
            limit=limit,
        )

    async def get_cross_community_edges(
        self, repository_id: str, community_id: int
    ) -> list[dict]:
        return await crud.get_cross_community_edges(
            self._session, repository_id, community_id
        )

    async def get_top_entry_points(
        self,
        repository_id: str,
        *,
        min_score: float = 0.3,
        limit: int = 20,
    ) -> list[GraphNode]:
        return await crud.get_top_entry_points(
            self._session, repository_id, min_score=min_score, limit=limit
        )

    async def get_node_degree_counts(
        self, repository_id: str, node_id: str
    ) -> dict[str, int]:
        return await crud.get_node_degree_counts(
            self._session, repository_id, node_id
        )

    async def bulk_upsert_external_systems(
        self, repository_id: str, systems: list[dict]
    ) -> dict[tuple[str, str], int]:
        return await crud.bulk_upsert_external_systems(
            self._session, repository_id, systems
        )

    async def link_graph_nodes_to_external_systems(
        self, repository_id: str, name_to_id: dict[str, int]
    ) -> int:
        return await crud.link_graph_nodes_to_external_systems(
            self._session, repository_id, name_to_id
        )

    async def list_external_systems(
        self, repository_id: str
    ) -> list[ExternalSystem]:
        return await crud.list_external_systems(self._session, repository_id)

    async def batch_upsert_symbols(
        self, repository_id: str, symbols: list
    ) -> None:
        await crud.batch_upsert_symbols(self._session, repository_id, symbols)
