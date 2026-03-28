"""Tests for /api/graph endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from tests.unit.server.conftest import create_test_repo


async def _populate_graph(session_factory, repo_id: str) -> None:
    """Insert test graph nodes and edges."""
    async with get_session(session_factory) as session:
        await crud.batch_upsert_graph_nodes(
            session,
            repo_id,
            [
                {
                    "node_id": "src/main.py",
                    "node_type": "file",
                    "language": "python",
                    "symbol_count": 3,
                    "pagerank": 0.8,
                    "betweenness": 0.5,
                    "community_id": 0,
                },
                {
                    "node_id": "src/utils.py",
                    "node_type": "file",
                    "language": "python",
                    "symbol_count": 5,
                    "pagerank": 0.3,
                    "betweenness": 0.1,
                    "community_id": 0,
                },
            ],
        )
        await crud.batch_upsert_graph_edges(
            session,
            repo_id,
            [
                {
                    "source_node_id": "src/main.py",
                    "target_node_id": "src/utils.py",
                    "imported_names_json": '["helper_func"]',
                },
            ],
        )


@pytest.mark.asyncio
async def test_export_graph_empty(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(f"/api/graph/{repo['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["nodes"] == []
    assert data["links"] == []


@pytest.mark.asyncio
async def test_export_graph_with_data(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _populate_graph(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/graph/{repo['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["nodes"]) == 2
    assert len(data["links"]) == 1
    assert data["links"][0]["source"] == "src/main.py"
    assert data["links"][0]["target"] == "src/utils.py"
    assert data["links"][0]["imported_names"] == ["helper_func"]


@pytest.mark.asyncio
async def test_export_graph_repo_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/graph/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dependency_path(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _populate_graph(app.state.session_factory, repo["id"])

    resp = await client.get(
        f"/api/graph/{repo['id']}/path",
        params={"from": "src/main.py", "to": "src/utils.py"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["distance"] == 1
    assert data["path"] == ["src/main.py", "src/utils.py"]


@pytest.mark.asyncio
async def test_dependency_path_no_path(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _populate_graph(app.state.session_factory, repo["id"])

    resp = await client.get(
        f"/api/graph/{repo['id']}/path",
        params={"from": "src/utils.py", "to": "src/main.py"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["distance"] == -1  # No reverse path

    # Visual context should be returned
    ctx = data["visual_context"]
    assert ctx is not None
    assert ctx["reverse_path"]["exists"] is True  # main -> utils exists
    assert ctx["disconnected"] is False
    assert "suggestion" in ctx
