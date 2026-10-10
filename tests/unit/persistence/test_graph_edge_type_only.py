"""Tests for graph_edges.type_only column persistence and rehydration."""

from __future__ import annotations

from repowise.core.analysis.health.refactoring.graph_signals import build_file_scc_index
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.persistence.crud import (
    batch_upsert_graph_edges,
    batch_upsert_graph_nodes,
    get_all_graph_edges,
    get_all_graph_nodes,
)
from tests.unit.persistence.helpers import insert_repo


async def test_graph_edge_type_only_round_trips_through_database(async_session) -> None:
    repo = await insert_repo(async_session)

    nodes = [
        {"node_id": "a.ts", "node_type": "file", "language": "typescript"},
        {"node_id": "b.ts", "node_type": "file", "language": "typescript"},
    ]
    edges = [
        {
            "source_node_id": "a.ts",
            "target_node_id": "b.ts",
            "imported_names_json": '["B"]',
            "edge_type": "imports",
            "type_only": True,
        },
        {
            "source_node_id": "b.ts",
            "target_node_id": "a.ts",
            "imported_names_json": '["A"]',
            "edge_type": "imports",
            "type_only": True,
        },
    ]

    await batch_upsert_graph_nodes(async_session, repo.id, nodes)
    await batch_upsert_graph_edges(async_session, repo.id, edges)
    await async_session.commit()

    loaded_nodes = await get_all_graph_nodes(async_session, repo.id)
    loaded_edges = await get_all_graph_edges(async_session, repo.id)

    assert len(loaded_edges) == 2
    for e in loaded_edges:
        assert e["type_only"] is True

    rehydrated = GraphBuilder.from_persisted(loaded_nodes, loaded_edges)
    assert rehydrated.graph()["a.ts"]["b.ts"]["type_only"] is True
    assert rehydrated.graph()["b.ts"]["a.ts"]["type_only"] is True
    assert rehydrated.cycle_subgraph().number_of_edges() == 0
    assert build_file_scc_index(rehydrated.graph()) == {}
