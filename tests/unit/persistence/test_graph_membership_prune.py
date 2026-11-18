"""Node retirement removes its materialized memberships in the same transaction."""

from __future__ import annotations

from types import SimpleNamespace

import networkx as nx
import pytest
from sqlalchemy import select

from repowise.core.persistence.crud import delete_graph_nodes
from repowise.core.persistence.models import GraphEdge, GraphNode, GraphNodeMembership
from repowise.core.pipeline.persist import (
    _prune_stale_file_rows,
    _prune_vanished_symbol_nodes,
    prune_absent_graph_rows,
    prune_absent_synthetic_nodes,
    prune_deleted_file_rows,
)
from tests.unit.persistence.helpers import insert_repo


@pytest.mark.parametrize("route", ["deleted", "stale", "full", "symbol", "synthetic"])
@pytest.mark.parametrize("rollback", [False, True])
async def test_node_prune_retires_memberships_atomically(async_session, tmp_path, route, rollback):
    kept = {"kept.py": "file", "kept.py::live": "symbol", "external:kept": "file"}
    if route == "symbol":
        removed = {"kept.py::old": "symbol"}
    elif route == "synthetic":
        removed = {"external:old": "file"}
    else:
        removed = {"old.py": "file", "old.py::old": "symbol"}
    nodes = kept | removed
    (tmp_path / "kept.py").write_text("def live(): pass\n", encoding="utf-8")
    repo = await insert_repo(async_session, local_path=str(tmp_path), name="target")
    other = await insert_repo(async_session, local_path=str(tmp_path / "other"), name="other")
    repo_id, other_id = repo.id, other.id
    for repository in (repo, other):
        for node_id, node_type in nodes.items():
            async_session.add(
                GraphNode(
                    repository_id=repository.id,
                    node_id=node_id,
                    node_type=node_type,
                    file_path=node_id.split("::", 1)[0] if node_type == "symbol" else None,
                )
            )
            async_session.add(
                GraphNodeMembership(
                    repository_id=repository.id,
                    node_id=node_id,
                    node_type=node_type,
                    scc_id=1 if node_type == "file" else None,
                    scc_size=2 if node_type == "file" else 0,
                    symbol_community_id=7 if node_type == "symbol" else None,
                )
            )
    async_session.add(
        GraphEdge(repository_id=repo.id, source_node_id="kept.py", target_node_id="kept.py::live")
    )
    await async_session.commit()

    graph = nx.DiGraph()
    graph.add_nodes_from(kept)
    builder = SimpleNamespace(graph=lambda: graph)
    if route == "deleted":
        count, refusals = await prune_deleted_file_rows(async_session, repo.id, tmp_path)
        assert (count, refusals) == (1, [])
    elif route == "stale":
        await _prune_stale_file_rows(async_session, repo.id, {"kept.py", "external:kept"}, set())
    elif route == "full":
        await prune_absent_graph_rows(
            async_session,
            repo.id,
            set(kept),
            [{"source_node_id": "kept.py", "target_node_id": "kept.py::live"}],
        )
    elif route == "symbol":
        await _prune_vanished_symbol_nodes(async_session, repo.id, builder, ["kept.py"])
    else:
        assert await prune_absent_synthetic_nodes(async_session, repo.id, builder) == 1

    for model in (GraphNode, GraphNodeMembership):
        actual = set(
            (
                await async_session.execute(
                    select(model.node_id).where(model.repository_id == repo.id)
                )
            )
            .scalars()
            .all()
        )
        assert actual == set(kept), model.__name__
    if rollback:
        await async_session.rollback()
    else:
        await async_session.commit()

    for repository_id, expected in (
        (repo_id, set(nodes) if rollback else set(kept)),
        (other_id, set(nodes)),
    ):
        for model in (GraphNode, GraphNodeMembership):
            actual = set(
                (
                    await async_session.execute(
                        select(model.node_id).where(model.repository_id == repository_id)
                    )
                )
                .scalars()
                .all()
            )
            assert actual == expected, model.__name__


async def test_node_retirement_batches_preserve_unselected_memberships(async_session):
    repo = await insert_repo(async_session)
    gone = [f"gone-{i}.py" for i in range(503)]
    for node_id in ["kept.py", *gone]:
        async_session.add(GraphNode(repository_id=repo.id, node_id=node_id, node_type="file"))
        async_session.add(
            GraphNodeMembership(repository_id=repo.id, node_id=node_id, node_type="file")
        )
    await async_session.commit()

    await delete_graph_nodes(async_session, repo.id, [])
    for model in (GraphNode, GraphNodeMembership):
        assert (
            len(
                (
                    await async_session.execute(
                        select(model.node_id).where(model.repository_id == repo.id)
                    )
                )
                .scalars()
                .all()
            )
            == 504
        )

    await delete_graph_nodes(async_session, repo.id, gone)
    await async_session.commit()
    for model in (GraphNode, GraphNodeMembership):
        assert (
            (
                await async_session.execute(
                    select(model.node_id).where(model.repository_id == repo.id)
                )
            )
            .scalars()
            .all()
        ) == ["kept.py"]
