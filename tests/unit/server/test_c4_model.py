"""Tests for build_model — the one-pass whole-model C4 build.

The point of these is agreement: the batched path must produce exactly what
looping the per-container path produces, or the export and the dashboard tell
users different stories about the same repo.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from httpx import AsyncClient

from repowise.core.persistence import (
    batch_upsert_graph_edges,
    batch_upsert_graph_nodes,
    bulk_upsert_external_systems,
    link_graph_nodes_to_external_systems,
)
from repowise.server.services.c4_builder import build_l2, build_l3, build_model


async def _create_repo(client: AsyncClient) -> dict:
    repo_dir = Path(tempfile.mkdtemp()) / "test-repo"
    repo_dir.mkdir(exist_ok=True)
    (repo_dir / ".git").mkdir(exist_ok=True)
    resp = await client.post(
        "/api/repos",
        json={
            "index": False,
            "name": "test-repo",
            "local_path": str(repo_dir),
            "url": "https://github.com/example/test-repo",
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def _seed(client: AsyncClient, app) -> str:
    """A repo with three containers, nested components, and two externals.

    Deliberately includes a file at a container root (the ``(root)`` bucket),
    a pass-through ``src`` directory, and a container whose path is a prefix
    of another's name — the three shapes the ownership rules get wrong when
    they are written twice.
    """
    repo = await _create_repo(client)
    repo_id = repo["id"]

    async with app.state.session_factory() as session:
        nodes = [
            # packages/core — files at the root plus a nested feature dir
            {
                "node_id": "packages/core/setup.py",
                "node_type": "file",
                "language": "python",
                "symbol_count": 1,
            },
            {
                "node_id": "packages/core/src/ingestion/parser.py",
                "node_type": "file",
                "language": "python",
                "symbol_count": 5,
            },
            {
                "node_id": "packages/core/src/ingestion/graph.py",
                "node_type": "file",
                "language": "python",
                "symbol_count": 3,
            },
            {
                "node_id": "packages/core/src/analysis/health.py",
                "node_type": "file",
                "language": "python",
                "symbol_count": 4,
            },
            # packages/core-extras — shares a prefix with packages/core
            {
                "node_id": "packages/core-extras/plugin.py",
                "node_type": "file",
                "language": "python",
                "symbol_count": 2,
            },
            # packages/web
            {
                "node_id": "packages/web/src/pages/index.tsx",
                "node_type": "file",
                "language": "typescript",
                "symbol_count": 2,
            },
            {
                "node_id": "packages/web/package.json",
                "node_type": "file",
                "language": "json",
                "symbol_count": 0,
            },
            {
                "node_id": "external:fastapi",
                "node_type": "file",
                "language": "python",
                "symbol_count": 0,
            },
            {
                "node_id": "external:react",
                "node_type": "file",
                "language": "typescript",
                "symbol_count": 0,
            },
        ]
        await batch_upsert_graph_nodes(session, repo_id, nodes)
        await batch_upsert_graph_edges(
            session,
            repo_id,
            [
                {
                    "source_node_id": "packages/web/src/pages/index.tsx",
                    "target_node_id": "packages/core/src/ingestion/graph.py",
                    "edge_type": "imports",
                },
                {
                    "source_node_id": "packages/core/src/analysis/health.py",
                    "target_node_id": "packages/core/src/ingestion/graph.py",
                    "edge_type": "imports",
                },
                {
                    "source_node_id": "packages/core/src/ingestion/parser.py",
                    "target_node_id": "external:fastapi",
                    "edge_type": "imports",
                },
                {
                    "source_node_id": "packages/web/src/pages/index.tsx",
                    "target_node_id": "external:react",
                    "edge_type": "imports",
                },
                {
                    "source_node_id": "packages/core-extras/plugin.py",
                    "target_node_id": "packages/core/src/ingestion/parser.py",
                    "edge_type": "imports",
                },
            ],
        )
        id_map = await bulk_upsert_external_systems(
            session,
            repo_id,
            [
                {
                    "name": "fastapi",
                    "display_name": "FastAPI",
                    "ecosystem": "pypi",
                    "category": "framework",
                    "version": "0.110",
                    "declared_in": "packages/core/pyproject.toml",
                    "is_dev_dep": False,
                },
                {
                    "name": "react",
                    "display_name": "React",
                    "ecosystem": "npm",
                    "category": "framework",
                    "version": "^18",
                    "declared_in": "packages/web/package.json",
                    "is_dev_dep": False,
                },
            ],
        )
        name_to_id = {n: sid for (n, _), sid in id_map.items()}
        await link_graph_nodes_to_external_systems(session, repo_id, name_to_id)
        await session.commit()
    return repo_id


async def test_containers_match_the_per_level_build(client: AsyncClient, app) -> None:
    repo_id = await _seed(client, app)
    async with app.state.session_factory() as session:
        l2 = await build_l2(session, repo_id)
        model = await build_model(session, repo_id)

    assert model.containers == l2.containers
    assert model.container_relations == l2.relations


async def test_components_match_the_per_container_build(client: AsyncClient, app) -> None:
    """The whole point of the batched path: identical output, one read."""
    repo_id = await _seed(client, app)
    async with app.state.session_factory() as session:
        model = await build_model(session, repo_id)
        per_container = {}
        for container in model.containers:
            l3 = await build_l3(session, repo_id, container.id)
            assert l3 is not None
            per_container[container.id] = l3.components

    assert model.components_by_container == per_container
    # And the fixture actually exercises the interesting shapes.
    assert per_container, "no containers detected — the fixture proves nothing"
    every_component = [c for comps in per_container.values() for c in comps]
    assert any(c.name == "(root)" for c in every_component)
    assert any(c.name == "ingestion" for c in every_component)


async def test_a_shared_path_prefix_does_not_steal_files(client: AsyncClient, app) -> None:
    """``packages/core`` must not absorb ``packages/core-extras``."""
    repo_id = await _seed(client, app)
    async with app.state.session_factory() as session:
        model = await build_model(session, repo_id)

    by_path = {c.path: c for c in model.containers}
    if "packages/core" in by_path and "packages/core-extras" in by_path:
        core_components = model.components_by_container[by_path["packages/core"].id]
        assert all("core-extras" not in c.path for c in core_components)


async def test_component_relations_keep_both_ends_at_component_level(
    client: AsyncClient, app
) -> None:
    """A cross-container edge names the real component, not its container."""
    repo_id = await _seed(client, app)
    async with app.state.session_factory() as session:
        model = await build_model(session, repo_id)

    component_ids = {c.id for comps in model.components_by_container.values() for c in comps}
    internal = [
        r
        for r in model.component_relations
        if r.source_id in component_ids and r.target_id in component_ids
    ]
    assert internal, "expected at least one component-to-component relation"


async def test_components_can_be_skipped(client: AsyncClient, app) -> None:
    repo_id = await _seed(client, app)
    async with app.state.session_factory() as session:
        model = await build_model(session, repo_id, include_components=False)

    assert model.containers
    assert model.components_by_container == {}
    assert model.component_relations == []
    # Containers and their relations are unaffected by the opt-out.
    async with app.state.session_factory() as session:
        full = await build_model(session, repo_id)
    assert model.containers == full.containers
    assert model.container_relations == full.container_relations


async def test_externals_are_pruned_to_the_ones_actually_used(client: AsyncClient, app) -> None:
    repo_id = await _seed(client, app)
    async with app.state.session_factory() as session:
        model = await build_model(session, repo_id)

    assert {e.name for e in model.external_systems} == {"fastapi", "react"}


async def test_empty_repo_builds_an_empty_model(client: AsyncClient, app) -> None:
    repo = await _create_repo(client)
    async with app.state.session_factory() as session:
        model = await build_model(session, repo["id"])

    assert model.containers == []
    assert model.components_by_container == {}
    assert model.external_systems == []
    assert model.system.name == "test-repo"
