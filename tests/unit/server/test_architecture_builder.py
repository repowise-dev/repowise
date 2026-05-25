from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import (
    batch_upsert_graph_edges,
    batch_upsert_graph_nodes,
    bulk_upsert_external_systems,
    link_graph_nodes_to_external_systems,
)
from repowise.core.persistence.crud import upsert_kg_layers, upsert_kg_tour_steps
from repowise.server.services.c4_builder.architecture import build_architecture_view


async def _create_repo(client: AsyncClient) -> dict:
    repo_dir = Path(tempfile.mkdtemp()) / "test-repo"
    repo_dir.mkdir(exist_ok=True)
    (repo_dir / ".git").mkdir(exist_ok=True)
    resp = await client.post(
        "/api/repos",
        json={
            "name": "test-repo",
            "local_path": str(repo_dir),
            "url": "https://github.com/example/test-repo",
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def _seed_graph(app, repo_id: str) -> None:
    async with app.state.session_factory() as session:
        nodes = [
            {"node_id": "src/main.py", "node_type": "file", "language": "python", "symbol_count": 3, "is_entry_point": True},
            {"node_id": "src/utils.py", "node_type": "file", "language": "python", "symbol_count": 8},
            {"node_id": "src/models.py", "node_type": "file", "language": "python", "symbol_count": 25},
            {"node_id": "lib/helpers.ts", "node_type": "file", "language": "typescript", "symbol_count": 2},
            {"node_id": "lib/index.ts", "node_type": "file", "language": "typescript", "symbol_count": 1, "is_entry_point": True},
            {"node_id": "tests/test_main.py", "node_type": "file", "language": "python", "symbol_count": 4, "is_test": True},
            {"node_id": "tests/test_utils.py", "node_type": "file", "language": "python", "symbol_count": 6, "is_test": True},
            {"node_id": "config/settings.py", "node_type": "file", "language": "python", "symbol_count": 2},
            {"node_id": "src/main.py::main", "node_type": "function", "language": "python", "symbol_count": 0, "file_path": "src/main.py", "name": "main", "start_line": 1, "end_line": 10},
            {"node_id": "src/models.py::User", "node_type": "class", "language": "python", "symbol_count": 0, "file_path": "src/models.py", "name": "User", "start_line": 5, "end_line": 30},
            {"node_id": "external:fastapi", "node_type": "file", "language": "python", "symbol_count": 0},
            {"node_id": "external:react", "node_type": "file", "language": "typescript", "symbol_count": 0},
        ]
        await batch_upsert_graph_nodes(session, repo_id, nodes)

        edges = [
            {"source_node_id": "src/main.py", "target_node_id": "src/utils.py", "edge_type": "imports"},
            {"source_node_id": "src/main.py", "target_node_id": "src/models.py", "edge_type": "imports"},
            {"source_node_id": "src/utils.py", "target_node_id": "src/models.py", "edge_type": "imports"},
            {"source_node_id": "lib/index.ts", "target_node_id": "lib/helpers.ts", "edge_type": "imports"},
            {"source_node_id": "tests/test_main.py", "target_node_id": "src/main.py", "edge_type": "imports"},
            {"source_node_id": "tests/test_utils.py", "target_node_id": "src/utils.py", "edge_type": "imports"},
            {"source_node_id": "src/main.py", "target_node_id": "config/settings.py", "edge_type": "imports"},
            {"source_node_id": "src/main.py", "target_node_id": "external:fastapi", "edge_type": "imports"},
            {"source_node_id": "lib/helpers.ts", "target_node_id": "external:react", "edge_type": "imports"},
            {"source_node_id": "src/main.py::main", "target_node_id": "src/utils.py", "edge_type": "calls"},
        ]
        await batch_upsert_graph_edges(session, repo_id, edges)

        id_map = await bulk_upsert_external_systems(session, repo_id, [
            {"name": "fastapi", "display_name": "FastAPI", "ecosystem": "pypi", "category": "framework", "version": "0.110", "declared_in": "pyproject.toml", "is_dev_dep": False},
            {"name": "react", "display_name": "React", "ecosystem": "npm", "category": "framework", "version": "^18", "declared_in": "package.json", "is_dev_dep": False},
        ])
        name_to_id = {n: sid for (n, _), sid in id_map.items()}
        await link_graph_nodes_to_external_systems(session, repo_id, name_to_id)
        await session.commit()


def _write_knowledge_graph(tmp_dir: Path) -> str:
    kg = {
        "version": "1.0.0",
        "project": {"name": "test-repo", "description": "A test project"},
        "layers": [
            {"id": "layer:api", "name": "API", "description": "API layer", "nodeIds": ["file:src/main.py", "file:src/utils.py"]},
            {"id": "layer:domain", "name": "Domain", "description": "Domain models", "nodeIds": ["file:src/models.py"]},
            {"id": "layer:frontend", "name": "Frontend", "description": "Frontend code", "nodeIds": ["file:lib/index.ts", "file:lib/helpers.ts"]},
        ],
        "tour": [
            {"order": 1, "title": "Entry Point", "description": "Start here", "nodeIds": ["file:src/main.py"]},
            {"order": 2, "title": "Models", "description": "Core models", "nodeIds": ["file:src/models.py"]},
            {"order": 3, "title": "Frontend", "description": "UI layer", "nodeIds": ["file:lib/index.ts"]},
        ],
    }
    kg_path = tmp_dir / "knowledge-graph.json"
    kg_path.write_text(json.dumps(kg))
    return str(kg_path)


async def test_build_architecture_view_basic(client: AsyncClient, app) -> None:
    repo = await _create_repo(client)
    await _seed_graph(app, repo["id"])

    async with app.state.session_factory() as session:
        view = await build_architecture_view(session, repo["id"])

    assert view.project_name == "test-repo"
    assert len(view.nodes) > 0
    assert len(view.edges) > 0
    assert len(view.layers) > 0
    assert view.total_files == 10
    assert "python" in view.languages
    assert "typescript" in view.languages


async def test_layer_assignment_from_db_kg(client: AsyncClient, app) -> None:
    repo = await _create_repo(client)
    await _seed_graph(app, repo["id"])

    async with app.state.session_factory() as session:
        await upsert_kg_layers(session, repo["id"], [
            {"id": "layer:api", "name": "API", "description": "API layer", "nodeIds": ["file:src/main.py", "file:src/utils.py"]},
            {"id": "layer:domain", "name": "Domain", "description": "Domain models", "nodeIds": ["file:src/models.py"]},
            {"id": "layer:frontend", "name": "Frontend", "description": "Frontend code", "nodeIds": ["file:lib/index.ts", "file:lib/helpers.ts"]},
        ])
        await session.commit()

    async with app.state.session_factory() as session:
        view = await build_architecture_view(session, repo["id"])

    layer_names = {l.name for l in view.layers}
    assert layer_names == {"API", "Domain", "Frontend"}
    api_layer = next(l for l in view.layers if l.name == "API")
    assert "src/main.py" in api_layer.node_ids


async def test_layer_assignment_from_communities(client: AsyncClient, app) -> None:
    repo = await _create_repo(client)
    repo_id = repo["id"]

    async with app.state.session_factory() as session:
        nodes = [
            {"node_id": "a/foo.py", "node_type": "file", "language": "python", "symbol_count": 1, "community_id": 1, "community_meta_json": json.dumps({"name": "Group A", "description": "First group"})},
            {"node_id": "a/bar.py", "node_type": "file", "language": "python", "symbol_count": 2, "community_id": 1},
            {"node_id": "b/baz.py", "node_type": "file", "language": "python", "symbol_count": 3, "community_id": 2, "community_meta_json": json.dumps({"name": "Group B"})},
        ]
        await batch_upsert_graph_nodes(session, repo_id, nodes)
        await session.commit()

    async with app.state.session_factory() as session:
        view = await build_architecture_view(session, repo_id)

    assert len(view.layers) == 2
    layer_ids = {l.id for l in view.layers}
    assert "layer:community-1" in layer_ids
    assert "layer:community-2" in layer_ids
    group_a = next(l for l in view.layers if l.id == "layer:community-1")
    assert group_a.name == "Group A"


async def test_layer_assignment_from_directories(client: AsyncClient, app) -> None:
    repo = await _create_repo(client)
    await _seed_graph(app, repo["id"])

    async with app.state.session_factory() as session:
        view = await build_architecture_view(session, repo["id"])

    layer_ids = {l.id for l in view.layers}
    assert "layer:dir-src" in layer_ids
    assert "layer:dir-lib" in layer_ids
    assert "layer:dir-tests" in layer_ids
    assert "layer:dir-config" in layer_ids


async def test_node_enrichment_flags(client: AsyncClient, app) -> None:
    repo = await _create_repo(client)
    await _seed_graph(app, repo["id"])

    async with app.state.session_factory() as session:
        view = await build_architecture_view(session, repo["id"])

    nodes_by_id = {n.id: n for n in view.nodes}

    main = nodes_by_id["src/main.py"]
    assert main.is_entry_point is True
    assert main.is_test is False
    assert main.complexity == "simple"

    test_node = nodes_by_id["tests/test_main.py"]
    assert test_node.is_test is True
    assert test_node.is_entry_point is False

    models = nodes_by_id["src/models.py"]
    assert models.complexity == "complex"

    utils = nodes_by_id["src/utils.py"]
    assert utils.complexity == "moderate"


async def test_edge_category_mapping(client: AsyncClient, app) -> None:
    repo = await _create_repo(client)
    await _seed_graph(app, repo["id"])

    async with app.state.session_factory() as session:
        view = await build_architecture_view(session, repo["id"])

    edge_types = {e.edge_type for e in view.edges}
    assert "imports" in edge_types
    for e in view.edges:
        assert e.direction == "forward"
        assert e.confidence > 0


async def test_symbols_excluded_by_default(client: AsyncClient, app) -> None:
    repo = await _create_repo(client)
    await _seed_graph(app, repo["id"])

    async with app.state.session_factory() as session:
        view = await build_architecture_view(session, repo["id"], include_symbols=False)

    node_types = {n.node_type for n in view.nodes}
    assert node_types == {"file"}


async def test_symbols_included_when_requested(client: AsyncClient, app) -> None:
    repo = await _create_repo(client)
    await _seed_graph(app, repo["id"])

    async with app.state.session_factory() as session:
        view = await build_architecture_view(session, repo["id"], include_symbols=True)

    node_types = {n.node_type for n in view.nodes}
    assert "function" in node_types
    assert "class" in node_types
    assert "file" in node_types

    fn_node = next(n for n in view.nodes if n.node_type == "function")
    assert fn_node.line_range is not None
    assert fn_node.line_range == (1, 10)


async def test_empty_repo(client: AsyncClient, app) -> None:
    repo = await _create_repo(client)

    async with app.state.session_factory() as session:
        view = await build_architecture_view(session, repo["id"])

    assert view.project_name == "test-repo"
    assert view.nodes == []
    assert view.edges == []
    assert view.layers == []
    assert view.total_files == 0
    assert view.total_edges == 0


async def test_tour_from_db(client: AsyncClient, app) -> None:
    repo = await _create_repo(client)
    await _seed_graph(app, repo["id"])

    async with app.state.session_factory() as session:
        await upsert_kg_tour_steps(session, repo["id"], [
            {"order": 1, "title": "Entry Point", "description": "Start here", "nodeIds": ["file:src/main.py"]},
            {"order": 2, "title": "Models", "description": "Core models", "nodeIds": ["file:src/models.py"]},
        ])
        await session.commit()

    async with app.state.session_factory() as session:
        view = await build_architecture_view(session, repo["id"])

    assert len(view.tour) == 2
    assert view.tour[0].title == "Entry Point"
    assert view.tour[0].order == 1
    assert "src/main.py" in view.tour[0].node_ids
    assert view.tour[1].title == "Models"
    assert view.tour[1].order == 2


async def test_db_layers_take_priority_over_communities(client: AsyncClient, app) -> None:
    """DB layers should win even when community_id is set on nodes."""
    repo = await _create_repo(client)
    repo_id = repo["id"]

    async with app.state.session_factory() as session:
        nodes = [
            {"node_id": "a/foo.py", "node_type": "file", "language": "python", "symbol_count": 1, "community_id": 1, "community_meta_json": json.dumps({"name": "Cluster"})},
            {"node_id": "a/bar.py", "node_type": "file", "language": "python", "symbol_count": 2, "community_id": 1},
            {"node_id": "b/baz.py", "node_type": "file", "language": "python", "symbol_count": 3, "community_id": 2},
        ]
        await batch_upsert_graph_nodes(session, repo_id, nodes)
        await upsert_kg_layers(session, repo_id, [
            {"id": "layer:custom", "name": "Custom Layer", "description": "From DB", "nodeIds": ["file:a/foo.py", "file:a/bar.py", "file:b/baz.py"]},
        ])
        await session.commit()

    async with app.state.session_factory() as session:
        view = await build_architecture_view(session, repo_id)

    assert len(view.layers) == 1
    assert view.layers[0].name == "Custom Layer"
    assert view.layers[0].id == "layer:custom"


async def test_auto_migrate_kg_file_to_db(client: AsyncClient, app) -> None:
    """When no DB layers exist but a KG file is on disk, layers are auto-migrated."""
    repo_dir = Path(tempfile.mkdtemp()) / "migrate-repo"
    repo_dir.mkdir(exist_ok=True)
    (repo_dir / ".git").mkdir(exist_ok=True)

    resp = await client.post(
        "/api/repos",
        json={
            "name": "migrate-repo",
            "local_path": str(repo_dir),
            "url": "https://github.com/example/migrate-repo",
        },
    )
    assert resp.status_code == 201
    repo = resp.json()
    repo_id = repo["id"]

    async with app.state.session_factory() as session:
        nodes = [
            {"node_id": "src/a.py", "node_type": "file", "language": "python", "symbol_count": 1},
            {"node_id": "src/b.py", "node_type": "file", "language": "python", "symbol_count": 2},
        ]
        await batch_upsert_graph_nodes(session, repo_id, nodes)
        await session.commit()

    rw_dir = repo_dir / ".repowise"
    rw_dir.mkdir(exist_ok=True)
    kg = {
        "layers": [
            {"id": "layer:migrated", "name": "Migrated", "description": "From file", "nodeIds": ["file:src/a.py", "file:src/b.py"]},
        ],
        "tour": [
            {"order": 1, "title": "Start", "description": "Begin here", "nodeIds": ["file:src/a.py"]},
        ],
    }
    (rw_dir / "knowledge-graph.json").write_text(json.dumps(kg))

    async with app.state.session_factory() as session:
        view = await build_architecture_view(session, repo_id)

    assert len(view.layers) == 1
    assert view.layers[0].name == "Migrated"
    assert len(view.tour) == 1
    assert view.tour[0].title == "Start"

    async with app.state.session_factory() as session:
        from repowise.core.persistence.crud import get_kg_layers as _get_layers
        db_layers = await _get_layers(session, repo_id)
        assert len(db_layers) >= 1
        assert db_layers[0].name == "Migrated"


async def test_tour_empty_when_no_data(client: AsyncClient, app) -> None:
    repo = await _create_repo(client)
    await _seed_graph(app, repo["id"])

    async with app.state.session_factory() as session:
        view = await build_architecture_view(session, repo["id"])

    assert view.tour == []


async def test_backward_compat_c4_endpoints(client: AsyncClient, app) -> None:
    repo = await _create_repo(client)
    await _seed_graph(app, repo["id"])

    resp_l1 = await client.get(f"/api/graph/{repo['id']}/c4/l1")
    assert resp_l1.status_code == 200
    assert resp_l1.json()["system"]["name"] == "test-repo"

    resp_l2 = await client.get(f"/api/graph/{repo['id']}/c4/l2")
    assert resp_l2.status_code == 200
    containers = resp_l2.json()["containers"]
    assert len(containers) > 0

    first_cid = containers[0]["id"]
    resp_l3 = await client.get(
        f"/api/graph/{repo['id']}/c4/l3",
        params={"container_id": first_cid},
    )
    assert resp_l3.status_code == 200
