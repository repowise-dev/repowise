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


# ---------------------------------------------------------------------------
# Community slice (Phase G4 — constellation blossom)
# ---------------------------------------------------------------------------


async def _populate_two_communities(session_factory, repo_id: str) -> None:
    """Two members in community 0, one in community 1, with a cross edge."""
    async with get_session(session_factory) as session:
        await crud.batch_upsert_graph_nodes(
            session,
            repo_id,
            [
                {
                    "node_id": "src/a.py",
                    "node_type": "file",
                    "language": "python",
                    "symbol_count": 3,
                    "pagerank": 0.8,
                    "betweenness": 0.5,
                    "community_id": 0,
                },
                {
                    "node_id": "src/b.py",
                    "node_type": "file",
                    "language": "python",
                    "symbol_count": 2,
                    "pagerank": 0.4,
                    "betweenness": 0.1,
                    "community_id": 0,
                },
                {
                    "node_id": "src/c.py",
                    "node_type": "file",
                    "language": "python",
                    "symbol_count": 1,
                    "pagerank": 0.2,
                    "betweenness": 0.0,
                    "community_id": 1,
                },
            ],
        )
        await crud.batch_upsert_graph_edges(
            session,
            repo_id,
            [
                # Intra-community (0)
                {"source_node_id": "src/a.py", "target_node_id": "src/b.py"},
                # Cross-community (0 -> 1): pulls c.py in as a boundary stub
                {"source_node_id": "src/b.py", "target_node_id": "src/c.py"},
            ],
        )


@pytest.mark.asyncio
async def test_community_slice_members_edges_and_boundary(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _populate_two_communities(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/graph/{repo['id']}/communities/0/slice")
    assert resp.status_code == 200
    data = resp.json()

    assert data["community_id"] == 0
    assert data["member_count"] == 2
    assert data["truncated"] is False

    by_id = {n["node_id"]: n for n in data["nodes"]}
    # Both members present and NOT boundary
    assert by_id["src/a.py"]["is_boundary"] is False
    assert by_id["src/b.py"]["is_boundary"] is False
    # Neighbor from community 1 pulled in as a boundary stub
    assert by_id["src/c.py"]["is_boundary"] is True

    # Edges: intra (a->b) + cross (b->c) both render
    pairs = {(link["source"], link["target"]) for link in data["links"]}
    assert ("src/a.py", "src/b.py") in pairs
    assert ("src/b.py", "src/c.py") in pairs


@pytest.mark.asyncio
async def test_community_slice_member_signals(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _populate_two_communities(app.state.session_factory, repo["id"])
    async with get_session(app.state.session_factory) as session:
        await crud.upsert_git_metadata(
            session,
            repository_id=repo["id"],
            file_path="src/a.py",
            is_hotspot=True,
            churn_percentile=0.99,
            primary_owner_name="Bob",
            commit_count_30d=5,
            commit_count_90d=12,
        )

    resp = await client.get(f"/api/graph/{repo['id']}/communities/0/slice")
    assert resp.status_code == 200
    by_id = {n["node_id"]: n for n in resp.json()["nodes"]}
    assert by_id["src/a.py"]["is_hotspot"] is True
    assert by_id["src/a.py"]["primary_owner"] == "Bob"
    # Boundary stub carries no signals
    assert by_id["src/c.py"]["is_hotspot"] is False


@pytest.mark.asyncio
async def test_community_slice_excludes_non_member_edges(client: AsyncClient, app) -> None:
    """The SQL membership filter must drop edges that touch no member.

    Seeds an extra node ``src/d.py`` in community 1 and an edge c->d that
    touches neither community-0 member. That edge must not appear in the slice,
    and the slice result must otherwise match the baseline expectations.
    """
    repo = await create_test_repo(client)
    async with get_session(app.state.session_factory) as session:
        await crud.batch_upsert_graph_nodes(
            session,
            repo["id"],
            [
                {
                    "node_id": "src/a.py",
                    "node_type": "file",
                    "language": "python",
                    "symbol_count": 3,
                    "pagerank": 0.8,
                    "community_id": 0,
                },
                {
                    "node_id": "src/b.py",
                    "node_type": "file",
                    "language": "python",
                    "symbol_count": 2,
                    "pagerank": 0.4,
                    "community_id": 0,
                },
                {
                    "node_id": "src/c.py",
                    "node_type": "file",
                    "language": "python",
                    "symbol_count": 1,
                    "pagerank": 0.2,
                    "community_id": 1,
                },
                {
                    "node_id": "src/d.py",
                    "node_type": "file",
                    "language": "python",
                    "symbol_count": 1,
                    "pagerank": 0.1,
                    "community_id": 1,
                },
            ],
        )
        await crud.batch_upsert_graph_edges(
            session,
            repo["id"],
            [
                # Intra-community 0 (touches members)
                {"source_node_id": "src/a.py", "target_node_id": "src/b.py"},
                # Cross 0->1 (touches a member -> boundary stub c.py)
                {"source_node_id": "src/b.py", "target_node_id": "src/c.py"},
                # Touches NO community-0 member: must be excluded entirely
                {"source_node_id": "src/c.py", "target_node_id": "src/d.py"},
            ],
        )

    resp = await client.get(f"/api/graph/{repo['id']}/communities/0/slice")
    assert resp.status_code == 200
    data = resp.json()

    assert data["community_id"] == 0
    assert data["member_count"] == 2
    assert data["truncated"] is False

    by_id = {n["node_id"]: n for n in data["nodes"]}
    assert by_id["src/a.py"]["is_boundary"] is False
    assert by_id["src/b.py"]["is_boundary"] is False
    assert by_id["src/c.py"]["is_boundary"] is True
    # d.py is only reachable via the excluded edge — it must not be pulled in.
    assert "src/d.py" not in by_id

    pairs = {(link["source"], link["target"]) for link in data["links"]}
    assert ("src/a.py", "src/b.py") in pairs
    assert ("src/b.py", "src/c.py") in pairs
    # The non-member-touching edge is excluded.
    assert ("src/c.py", "src/d.py") not in pairs


@pytest.mark.asyncio
async def test_community_slice_empty_community(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _populate_two_communities(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/graph/{repo['id']}/communities/999/slice")
    assert resp.status_code == 200
    data = resp.json()
    assert data["nodes"] == []
    assert data["links"] == []
    assert data["member_count"] == 0


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
