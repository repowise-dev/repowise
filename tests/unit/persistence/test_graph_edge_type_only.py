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


async def test_graph_edge_deferred_round_trips_through_database(async_session) -> None:
    """A function-local import's mark survives rehydration, so an update does
    not bring back the lazy-import cycle the full index suppressed."""
    repo = await insert_repo(async_session)
    nodes = [
        {"node_id": "a.py", "node_type": "file", "language": "python"},
        {"node_id": "b.py", "node_type": "file", "language": "python"},
    ]
    edges = [
        {"source_node_id": "a.py", "target_node_id": "b.py", "edge_type": "imports"},
        {
            "source_node_id": "b.py",
            "target_node_id": "a.py",
            "edge_type": "imports",
            "deferred": True,
        },
    ]
    await batch_upsert_graph_nodes(async_session, repo.id, nodes)
    await batch_upsert_graph_edges(async_session, repo.id, edges)
    await async_session.commit()

    loaded = {
        (e["source_node_id"], e["target_node_id"]): e
        for e in await get_all_graph_edges(async_session, repo.id)
    }
    assert loaded[("b.py", "a.py")]["deferred"] is True
    assert loaded[("a.py", "b.py")]["deferred"] is False

    rehydrated = GraphBuilder.from_persisted(
        await get_all_graph_nodes(async_session, repo.id), list(loaded.values())
    )
    assert rehydrated.graph()["b.py"]["a.py"]["deferred"] is True
    assert build_file_scc_index(rehydrated.graph()) == {}


def test_incremental_edge_path_carries_the_load_kind() -> None:
    """The incremental writer emits the same row as the full one, marks included."""
    from types import SimpleNamespace

    from repowise.core.pipeline.persist import _changed_file_edges, _edge_row

    builder = GraphBuilder()
    graph = builder.graph()
    graph.add_node("a.py", node_type="file")
    graph.add_node("b.py", node_type="file")
    graph.add_edge("a.py", "b.py", edge_type="imports", type_only=False, deferred=True)
    parsed = [SimpleNamespace(file_info=SimpleNamespace(path="a.py"))]

    _, rows = _changed_file_edges(builder, parsed, ["a.py"])
    assert rows == [_edge_row("a.py", "b.py", graph["a.py"]["b.py"])]
    assert rows[0]["deferred"] is True and rows[0]["type_only"] is False
