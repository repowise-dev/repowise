"""Tests for ``get_node_degree_counts_bulk`` with ``exclude_spec``."""

from __future__ import annotations

import pathspec
import pytest

from repowise.core.persistence.crud.graph import (
    get_node_degree_counts,
    get_node_degree_counts_bulk,
)
from repowise.core.persistence.models import GraphEdge, GraphNode
from repowise.core.persistence.stores import SqlIndexStore
from tests.unit.persistence.helpers import insert_repo


async def _seed_graph(async_session, repo_id: str) -> None:
    nodes = [
        GraphNode(repository_id=repo_id, node_id="src/a.py", node_type="file", name="a.py"),
        GraphNode(repository_id=repo_id, node_id="src/b.py", node_type="file", name="b.py"),
        GraphNode(repository_id=repo_id, node_id="gen/out.py", node_type="file", name="out.py"),
        GraphNode(
            repository_id=repo_id, node_id="src/isolated.py", node_type="file", name="isolated.py"
        ),
    ]
    edges = [
        # Excluded source -> allowed target
        GraphEdge(
            repository_id=repo_id,
            source_node_id="gen/out.py",
            target_node_id="src/a.py",
            edge_type="imports",
        ),
        # Allowed source -> allowed target
        GraphEdge(
            repository_id=repo_id,
            source_node_id="src/a.py",
            target_node_id="src/b.py",
            edge_type="imports",
        ),
        # Allowed source -> excluded target
        GraphEdge(
            repository_id=repo_id,
            source_node_id="src/b.py",
            target_node_id="gen/out.py",
            edge_type="imports",
        ),
        # Different edge type between allowed nodes
        GraphEdge(
            repository_id=repo_id,
            source_node_id="src/b.py",
            target_node_id="src/a.py",
            edge_type="calls",
        ),
        # Self-loop on allowed node
        GraphEdge(
            repository_id=repo_id,
            source_node_id="src/a.py",
            target_node_id="src/a.py",
            edge_type="imports",
        ),
    ]
    async_session.add_all(nodes + edges)
    await async_session.commit()


@pytest.mark.asyncio
async def test_bulk_matches_single_with_exclude_spec(async_session):
    repo = await insert_repo(async_session)
    await _seed_graph(async_session, repo.id)

    spec = pathspec.PathSpec.from_lines("gitwildmatch", ["gen/"])
    present_nodes = ["src/a.py", "src/b.py", "gen/out.py", "src/isolated.py"]
    query_nodes = [*present_nodes, "src/missing.py"]

    for edge_types in (None, ["imports"], ["calls"]):
        bulk = await get_node_degree_counts_bulk(
            async_session,
            repo.id,
            query_nodes,
            edge_types=edge_types,
            exclude_spec=spec,
        )
        assert "src/missing.py" not in bulk
        assert set(bulk) == set(present_nodes)
        for node_id in present_nodes:
            single = await get_node_degree_counts(
                async_session,
                repo.id,
                node_id,
                edge_types=edge_types,
                exclude_spec=spec,
            )
            assert bulk[node_id] == single

    # Without exclude_spec, the COUNT(*) path includes edges touching gen/out.py
    bulk_all = await get_node_degree_counts_bulk(
        async_session, repo.id, present_nodes, edge_types=["imports"]
    )
    assert bulk_all["src/a.py"] == {"in_degree": 2, "out_degree": 2}
    assert bulk_all["src/b.py"] == {"in_degree": 1, "out_degree": 1}

    bulk_filtered = await get_node_degree_counts_bulk(
        async_session,
        repo.id,
        present_nodes,
        edge_types=["imports"],
        exclude_spec=spec,
    )
    assert bulk_filtered["src/a.py"] == {"in_degree": 1, "out_degree": 2}
    assert bulk_filtered["src/b.py"] == {"in_degree": 1, "out_degree": 0}
    assert bulk_filtered["gen/out.py"] == {"in_degree": 0, "out_degree": 0}


@pytest.mark.asyncio
async def test_sql_index_store_degree_counts_protocol(async_session):
    repo = await insert_repo(async_session)
    await _seed_graph(async_session, repo.id)
    store = SqlIndexStore(async_session)
    spec = pathspec.PathSpec.from_lines("gitwildmatch", ["gen/"])

    single = await store.get_node_degree_counts(
        repo.id, "src/a.py", edge_types=["imports"], exclude_spec=spec
    )
    bulk = await store.get_node_degree_counts_bulk(
        repo.id, ["src/a.py", "src/b.py"], edge_types=["imports"], exclude_spec=spec
    )
    assert bulk["src/a.py"] == single == {"in_degree": 1, "out_degree": 2}
    assert bulk["src/b.py"] == {"in_degree": 1, "out_degree": 0}
