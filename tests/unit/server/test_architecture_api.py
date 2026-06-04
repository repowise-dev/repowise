from __future__ import annotations

import tempfile
from pathlib import Path

from httpx import AsyncClient

from repowise.core.persistence import (
    batch_upsert_graph_edges,
    batch_upsert_graph_nodes,
    bulk_upsert_external_systems,
    link_graph_nodes_to_external_systems,
    upsert_kg_layers,
    upsert_kg_node_meta,
    upsert_kg_project_meta,
    upsert_kg_tour_steps,
)


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


async def _seed(client: AsyncClient, app) -> str:
    repo = await _create_repo(client)
    repo_id = repo["id"]

    async with app.state.session_factory() as session:
        nodes = [
            {"node_id": "src/app.py", "node_type": "file", "language": "python", "symbol_count": 5, "is_entry_point": True},
            {"node_id": "src/db.py", "node_type": "file", "language": "python", "symbol_count": 12},
            {"node_id": "src/app.py::run", "node_type": "function", "language": "python", "symbol_count": 0, "file_path": "src/app.py", "name": "run", "start_line": 1, "end_line": 20},
            {"node_id": "external:sqlalchemy", "node_type": "file", "language": "python", "symbol_count": 0},
        ]
        await batch_upsert_graph_nodes(session, repo_id, nodes)
        await batch_upsert_graph_edges(session, repo_id, [
            {"source_node_id": "src/app.py", "target_node_id": "src/db.py", "edge_type": "imports"},
            {"source_node_id": "src/db.py", "target_node_id": "external:sqlalchemy", "edge_type": "imports"},
        ])
        id_map = await bulk_upsert_external_systems(session, repo_id, [
            {"name": "sqlalchemy", "display_name": "SQLAlchemy", "ecosystem": "pypi", "category": "library", "version": "2.0", "declared_in": "pyproject.toml", "is_dev_dep": False},
        ])
        name_to_id = {n: sid for (n, _), sid in id_map.items()}
        await link_graph_nodes_to_external_systems(session, repo_id, name_to_id)
        await session.commit()
    return repo_id


async def test_api_architecture_endpoint(client: AsyncClient, app) -> None:
    repo_id = await _seed(client, app)

    resp = await client.get(f"/api/graph/{repo_id}/architecture-view")
    assert resp.status_code == 200

    body = resp.json()
    assert body["project_name"] == "test-repo"
    assert body["total_files"] == 3
    assert len(body["nodes"]) == 3
    assert len(body["edges"]) > 0
    assert len(body["layers"]) > 0
    assert "python" in body["languages"]

    node_ids = {n["id"] for n in body["nodes"]}
    assert "src/app.py" in node_ids
    assert "src/db.py" in node_ids

    for node in body["nodes"]:
        assert "node_type" in node
        assert "complexity" in node
        assert "pagerank_percentile" in node

    for edge in body["edges"]:
        assert edge["direction"] == "forward"
        assert edge["confidence"] > 0


async def test_api_architecture_curated_fields(client: AsyncClient, app) -> None:
    """Curated KG fields (sub_groups, tour, entry points, node meta) reach the
    HTTP response — the Phase-A acceptance, DB-served path."""
    repo_id = await _seed(client, app)

    async with app.state.session_factory() as session:
        await upsert_kg_layers(session, repo_id, [
            {
                "id": "layer:api",
                "name": "API",
                "description": "Service edge",
                "nodeIds": ["file:src/app.py", "file:src/db.py"],
                "display_order": 0,
                "subGroups": [
                    {"id": "layer:api:app", "name": "app", "nodeIds": ["file:src/app.py"]},
                    {"id": "layer:api:db", "name": "db", "nodeIds": ["file:src/db.py"]},
                ],
            },
        ])
        await upsert_kg_tour_steps(session, repo_id, [
            {
                "order": 1,
                "title": "app.py",
                "target_path": "src/app.py",
                "page_type": "file_page",
                "depth": 0,
                "kind": "code",
                "reason": "Top of the stack.",
                "layer_id": "layer:api",
            },
        ])
        await upsert_kg_project_meta(
            session, repo_id,
            entry_points=["src/app.py"],
            entry_candidates=["src/app.py", "src/db.py"],
        )
        await upsert_kg_node_meta(session, repo_id, [
            {"id": "src/app.py", "type": "file",
             "summary": "Curated app summary.", "tags": ["entry_point", "python"]},
        ])
        await session.commit()

    resp = await client.get(f"/api/graph/{repo_id}/architecture-view")
    assert resp.status_code == 200
    body = resp.json()

    layer = next(la for la in body["layers"] if la["id"] == "layer:api")
    assert layer["display_order"] == 0
    assert layer["sub_groups"] == [
        {"id": "layer:api:app", "name": "app", "node_ids": ["src/app.py"]},
        {"id": "layer:api:db", "name": "db", "node_ids": ["src/db.py"]},
    ]

    step = body["tour"][0]
    assert step["target_path"] == "src/app.py"
    assert step["layer_id"] == "layer:api"
    assert step["reason"] == "Top of the stack."
    assert step["depth"] == 0
    assert step["kind"] == "code"
    assert step["page_type"] == "file_page"
    assert step["node_ids"] == ["src/app.py"]

    assert body["entry_points"] == ["src/app.py"]
    assert body["entry_candidates"] == ["src/app.py", "src/db.py"]

    app_node = next(n for n in body["nodes"] if n["id"] == "src/app.py")
    assert app_node["summary"] == "Curated app summary."
    assert app_node["tags"] == ["entry_point", "python"]


async def test_api_architecture_uncurated_defaults(client: AsyncClient, app) -> None:
    """Flag-off contract: uncurated repos serve the new keys as safe defaults."""
    repo_id = await _seed(client, app)

    resp = await client.get(f"/api/graph/{repo_id}/architecture-view")
    assert resp.status_code == 200
    body = resp.json()

    assert body["entry_points"] == []
    assert body["entry_candidates"] == []
    for layer in body["layers"]:
        assert layer["sub_groups"] == []
    for step in body["tour"]:
        assert step["target_path"] is None
        assert step["kind"] == ""


async def test_api_architecture_with_symbols(client: AsyncClient, app) -> None:
    repo_id = await _seed(client, app)

    resp = await client.get(f"/api/graph/{repo_id}/architecture-view", params={"include_symbols": "true"})
    assert resp.status_code == 200

    body = resp.json()
    node_types = {n["node_type"] for n in body["nodes"]}
    assert "function" in node_types
    assert "file" in node_types

    fn_node = next(n for n in body["nodes"] if n["node_type"] == "function")
    assert fn_node["name"] == "run"
    assert fn_node["line_range"] == [1, 20]
    assert fn_node["file_path"] == "src/app.py"
