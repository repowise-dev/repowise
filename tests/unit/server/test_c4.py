"""HTTP-level tests for /api/graph/{repo_id}/c4/{l1,l2,l3}."""

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


async def create_test_repo(client: AsyncClient) -> dict:
    """Local copy of tests.unit.server.conftest.create_test_repo so this
    module collects standalone (conftest helpers are not importable as a
    module from every pytest entry point)."""
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


async def _seed_two_containers(client: AsyncClient, app) -> str:
    """Create a repo + a tiny graph + 2 external deps. Returns repo_id."""
    repo = await create_test_repo(client)
    repo_id = repo["id"]

    async with app.state.session_factory() as session:
        nodes = [
            {"node_id": "packages/core/parser.py", "node_type": "file", "language": "python", "symbol_count": 2},
            {"node_id": "packages/core/graph.py", "node_type": "file", "language": "python", "symbol_count": 3},
            {"node_id": "packages/web/page.tsx", "node_type": "file", "language": "typescript", "symbol_count": 1},
            {"node_id": "external:fastapi", "node_type": "file", "language": "python", "symbol_count": 0},
            {"node_id": "external:react", "node_type": "file", "language": "typescript", "symbol_count": 0},
        ]
        await batch_upsert_graph_nodes(session, repo_id, nodes)
        await batch_upsert_graph_edges(session, repo_id, [
            {"source_node_id": "packages/web/page.tsx", "target_node_id": "packages/core/graph.py", "edge_type": "imports"},
            {"source_node_id": "packages/core/parser.py", "target_node_id": "external:fastapi", "edge_type": "imports"},
            {"source_node_id": "packages/web/page.tsx", "target_node_id": "external:react", "edge_type": "imports"},
        ])
        id_map = await bulk_upsert_external_systems(session, repo_id, [
            {"name": "fastapi", "display_name": "FastAPI", "ecosystem": "pypi", "category": "framework", "version": "0.110", "declared_in": "packages/core/pyproject.toml", "is_dev_dep": False},
            {"name": "react", "display_name": "React", "ecosystem": "npm", "category": "framework", "version": "^18", "declared_in": "packages/web/package.json", "is_dev_dep": False},
        ])
        name_to_id = {n: sid for (n, _), sid in id_map.items()}
        await link_graph_nodes_to_external_systems(session, repo_id, name_to_id)
        await session.commit()
    return repo_id


async def test_l1_endpoint_returns_system_and_externals(client: AsyncClient, app) -> None:
    repo_id = await _seed_two_containers(client, app)
    resp = await client.get(f"/api/graph/{repo_id}/c4/l1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["system"]["name"] == "test-repo"
    assert [p["id"] for p in body["people"]] == ["person:user"]
    assert {e["name"] for e in body["external_systems"]} == {"fastapi", "react"}
    # User → system + system → 2 externals
    assert len(body["relations"]) == 3


async def test_l2_endpoint_returns_containers_and_aggregated_edges(client: AsyncClient, app) -> None:
    repo_id = await _seed_two_containers(client, app)
    resp = await client.get(f"/api/graph/{repo_id}/c4/l2")
    assert resp.status_code == 200
    body = resp.json()
    paths = {c["path"] for c in body["containers"]}
    assert paths == {"packages/core", "packages/web"}

    edges = {(r["source_id"], r["target_id"]) for r in body["relations"]}
    assert ("pkg:packages/web", "pkg:packages/core") in edges
    assert ("pkg:packages/core", "ext:fastapi") in edges
    assert ("pkg:packages/web", "ext:react") in edges


async def test_l3_endpoint_requires_container_id(client: AsyncClient, app) -> None:
    repo_id = await _seed_two_containers(client, app)
    resp = await client.get(f"/api/graph/{repo_id}/c4/l3")
    assert resp.status_code == 422  # missing query param


async def test_l3_endpoint_returns_components_for_container(client: AsyncClient, app) -> None:
    repo_id = await _seed_two_containers(client, app)
    resp = await client.get(
        f"/api/graph/{repo_id}/c4/l3",
        params={"container_id": "pkg:packages/core"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["container"]["path"] == "packages/core"
    # All files in packages/core sit at the container root → _root component
    comp_names = {c["name"] for c in body["components"]}
    assert comp_names == {"_root"}
    # Only fastapi (used from packages/core), react is filtered out
    assert {e["name"] for e in body["external_systems"]} == {"fastapi"}


async def test_l3_endpoint_returns_404_for_unknown_container(client: AsyncClient, app) -> None:
    repo_id = await _seed_two_containers(client, app)
    resp = await client.get(
        f"/api/graph/{repo_id}/c4/l3",
        params={"container_id": "pkg:does/not/exist"},
    )
    assert resp.status_code == 404


async def test_l1_endpoint_on_empty_repo(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(f"/api/graph/{repo['id']}/c4/l1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["external_systems"] == []
    assert body["system"]["name"] == "test-repo"
