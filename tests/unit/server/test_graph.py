"""Tests for /api/graph endpoints."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import DeadCodeFinding, DecisionRecord
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


# ---------------------------------------------------------------------------
# Cross-link signal enrichment (Phase A)
# ---------------------------------------------------------------------------


async def _attach_signals(session_factory, repo_id: str) -> None:
    """Attach hotspot, dead-code, and decision signals to src/main.py."""
    async with get_session(session_factory) as session:
        await crud.upsert_git_metadata(
            session,
            repository_id=repo_id,
            file_path="src/main.py",
            is_hotspot=True,
            churn_percentile=0.95,
            primary_owner_name="Alice",
            commit_count_30d=10,
            commit_count_90d=20,
        )
        session.add(
            DeadCodeFinding(
                repository_id=repo_id,
                file_path="src/utils.py",
                kind="unreachable_file",
                status="open",
                confidence=0.9,
            )
        )
        session.add(
            DecisionRecord(
                repository_id=repo_id,
                title="Adopt FastAPI",
                status="active",
                source="cli",
                affected_files_json=json.dumps(["src/main.py"]),
            )
        )
        await session.flush()


@pytest.mark.asyncio
async def test_export_graph_carries_cross_link_signals(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _populate_graph(app.state.session_factory, repo["id"])
    await _attach_signals(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/graph/{repo['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["truncated"] is False
    assert data["total_node_count"] == 2

    by_id = {n["node_id"]: n for n in data["nodes"]}
    main = by_id["src/main.py"]
    utils = by_id["src/utils.py"]

    assert main["is_hotspot"] is True
    assert main["churn_percentile"] == pytest.approx(0.95)
    assert main["primary_owner"] == "Alice"
    assert main["has_decision"] is True
    assert main["is_dead"] is False

    assert utils["is_dead"] is True
    assert utils["dead_confidence"] == pytest.approx(0.9)
    assert utils["is_hotspot"] is False
    assert utils["has_decision"] is False


@pytest.mark.asyncio
async def test_export_graph_truncation(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _populate_graph(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/graph/{repo['id']}", params={"limit": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert data["truncated"] is True
    assert data["total_node_count"] == 2
    assert len(data["nodes"]) == 1
    # Top-N by PageRank: main.py (0.8) outranks utils.py (0.3)
    assert data["nodes"][0]["node_id"] == "src/main.py"
    # Edges pointing to filtered-out nodes must be dropped
    assert data["links"] == []


@pytest.mark.asyncio
async def test_architecture_graph(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _populate_graph(app.state.session_factory, repo["id"])
    await _attach_signals(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/graph/{repo['id']}/architecture")
    assert resp.status_code == 200
    data = resp.json()

    # Both seeded nodes share community 0
    assert len(data["nodes"]) == 1
    super_node = data["nodes"][0]
    assert super_node["community_id"] == 0
    assert super_node["member_count"] == 2
    assert super_node["hotspot_count"] == 1
    assert super_node["dead_count"] == 1
    assert super_node["has_decision"] is True
    assert "python" in super_node["languages"]
    # Same-community edges are collapsed away
    assert data["edges"] == []


@pytest.mark.asyncio
async def test_module_graph_aggregates_signals(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _populate_graph(app.state.session_factory, repo["id"])
    await _attach_signals(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/graph/{repo['id']}/modules")
    assert resp.status_code == 200
    data = resp.json()
    by_id = {m["module_id"]: m for m in data["nodes"]}
    src = by_id["src"]
    assert src["file_count"] == 2
    assert src["hotspot_count"] == 1
    assert src["dead_count"] == 1
    assert src["has_decision"] is True
    assert src["primary_owner"] == "Alice"
