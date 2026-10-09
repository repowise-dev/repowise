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
    # End-to-end test: verify nodes are fully populated from database through API
    assert data["nodes"][0]["symbol_count"] == 3
    assert data["nodes"][1]["symbol_count"] == 5


@pytest.mark.asyncio
async def test_export_graph_carries_edge_type(client: AsyncClient, app) -> None:
    """Edges carry their semantic type, not just imported_names.

    Without this the client can only infer edge kind from `imported_names`
    being empty, which collapses every `defines`/`calls`/`co_changes` row into
    one bucket. Both columns are populated on the row; they were simply never
    serialized.
    """
    repo = await create_test_repo(client)
    session_factory = app.state.session_factory
    await _populate_graph(session_factory, repo["id"])
    async with get_session(session_factory) as session:
        await crud.batch_upsert_graph_edges(
            session,
            repo["id"],
            [
                {
                    "source_node_id": "src/utils.py",
                    "target_node_id": "src/main.py",
                    "imported_names_json": "[]",
                    "edge_type": "calls",
                    "confidence": 0.5,
                },
            ],
        )

    resp = await client.get(f"/api/graph/{repo['id']}")
    assert resp.status_code == 200
    links = {(link["source"], link["target"]): link for link in resp.json()["links"]}

    calls = links[("src/utils.py", "src/main.py")]
    assert calls["edge_type"] == "calls"
    assert calls["confidence"] == 0.5

    # The default edge_type still round-trips, so a client can always branch on
    # it rather than falling back to the empty-imported_names heuristic.
    imports = links[("src/main.py", "src/utils.py")]
    assert imports["edge_type"] == "imports"


@pytest.mark.asyncio
async def test_export_graph_excludes_symbol_nodes(client: AsyncClient, app) -> None:
    """Symbol nodes never reach the export, and don't count toward the total.

    `graph_nodes` holds a row per extracted symbol as well as per file, and
    PageRank ranks both kinds together — so a symbol can outrank a real file and
    take its slot under the node cap, then render as a file circle with a
    `node_id` (`path::Symbol`) that is not a path. The client has no way to tell
    them apart: `node_type` is not serialized.
    """
    repo = await create_test_repo(client)
    session_factory = app.state.session_factory
    await _populate_graph(session_factory, repo["id"])
    async with get_session(session_factory) as session:
        await crud.batch_upsert_graph_nodes(
            session,
            repo["id"],
            [
                {
                    # Outranks both files, so a pagerank-ordered export without
                    # the filter would put it first.
                    "node_id": "src/main.py::main",
                    "node_type": "symbol",
                    "language": "python",
                    "symbol_count": 1,
                    "pagerank": 0.99,
                    "betweenness": 0.9,
                    "community_id": 0,
                },
            ],
        )

    resp = await client.get(f"/api/graph/{repo['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert [n["node_id"] for n in data["nodes"]] == ["src/main.py", "src/utils.py"]
    # The total is a file count too, so "1,500 of N" reads honestly.
    assert data["total_node_count"] == 2


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

    # Overlay counts: untruncated response has everything in view.
    assert data["dead_total"] == 1
    assert data["dead_in_view"] == 1
    assert data["hot_total"] == 1
    assert data["hot_in_view"] == 1


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


async def _populate_ranked_graph_with_flags(session_factory, repo_id: str) -> None:
    """Three high-PageRank files + one dead file and one hotspot that both
    rank BELOW the PageRank cutoff — the shape that used to silently drop
    every overlay node."""
    async with get_session(session_factory) as session:
        await crud.batch_upsert_graph_nodes(
            session,
            repo_id,
            [
                {
                    "node_id": f"src/core_{i}.py",
                    "node_type": "file",
                    "language": "python",
                    "symbol_count": 1,
                    "pagerank": 0.9 - i * 0.1,
                    "community_id": 0,
                }
                for i in range(3)
            ]
            + [
                {
                    "node_id": "src/orphan.py",
                    "node_type": "file",
                    "language": "python",
                    "symbol_count": 1,
                    "pagerank": 0.01,
                    "community_id": 0,
                },
                {
                    "node_id": "src/churny.py",
                    "node_type": "file",
                    "language": "python",
                    "symbol_count": 1,
                    "pagerank": 0.02,
                    "community_id": 0,
                },
            ],
        )
        session.add(
            DeadCodeFinding(
                repository_id=repo_id,
                file_path="src/orphan.py",
                kind="unreachable_file",
                status="open",
                confidence=0.9,
            )
        )
        await crud.upsert_git_metadata(
            session,
            repository_id=repo_id,
            file_path="src/churny.py",
            is_hotspot=True,
            churn_percentile=0.99,
            commit_count_30d=40,
            commit_count_90d=90,
        )
        await session.flush()


@pytest.mark.asyncio
async def test_export_graph_truncation_reserves_dead_and_hot_nodes(
    client: AsyncClient, app
) -> None:
    """Dead/hot files must survive truncation even with rock-bottom PageRank."""
    repo = await create_test_repo(client)
    await _populate_ranked_graph_with_flags(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/graph/{repo['id']}", params={"limit": 3})
    assert resp.status_code == 200
    data = resp.json()
    assert data["truncated"] is True
    assert data["total_node_count"] == 5

    kept = {n["node_id"] for n in data["nodes"]}
    # Reserved slots: the dead file and the hotspot are kept; the remaining
    # budget fills by PageRank (core_0 only).
    assert kept == {"src/orphan.py", "src/churny.py", "src/core_0.py"}

    assert data["dead_total"] == 1
    assert data["dead_in_view"] == 1
    assert data["hot_total"] == 1
    assert data["hot_in_view"] == 1


@pytest.mark.asyncio
async def test_export_graph_truncation_reserves_flow_members(client: AsyncClient, app) -> None:
    """The files an execution-flow trace runs through must survive truncation.

    `calls` edges only join symbol nodes, so the trace is a list of
    `file.py::symbol` ids while the export carries files — the reservation is
    for the containing files, which is what the canvas can highlight.
    """
    repo = await create_test_repo(client)
    async with get_session(app.state.session_factory) as session:
        await crud.batch_upsert_graph_nodes(
            session,
            repo["id"],
            [
                {
                    "node_id": f"src/core_{i}.py",
                    "node_type": "file",
                    "language": "python",
                    "symbol_count": 1,
                    "pagerank": 0.9 - i * 0.1,
                    "community_id": 0,
                }
                for i in range(3)
            ]
            + [
                # The files the traced symbols live in. Low PageRank, so only
                # the flow reservation can keep them under a limit of 3.
                {
                    "node_id": path,
                    "node_type": "file",
                    "language": "python",
                    "symbol_count": 1,
                    "pagerank": 0.001,
                    "community_id": 0,
                }
                for path in ("src/api.py", "src/service.py")
            ]
            + [
                {
                    "node_id": "src/api.py::handler",
                    "node_type": "symbol",
                    "language": "python",
                    "symbol_count": 0,
                    "pagerank": 0.03,
                    "community_id": 0,
                    "name": "handler",
                    "kind": "function",
                    "file_path": "src/api.py",
                    "community_meta_json": json.dumps({"entry_point_score": 0.9}),
                },
                {
                    "node_id": "src/service.py::run",
                    "node_type": "symbol",
                    "language": "python",
                    "symbol_count": 0,
                    "pagerank": 0.01,
                    "community_id": 0,
                    "name": "run",
                    "kind": "function",
                    "file_path": "src/service.py",
                },
            ],
        )
        await crud.batch_upsert_graph_edges(
            session,
            repo["id"],
            [
                {
                    "source_node_id": "src/api.py::handler",
                    "target_node_id": "src/service.py::run",
                    "edge_type": "calls",
                    "confidence": 0.95,
                },
            ],
        )

    resp = await client.get(f"/api/graph/{repo['id']}", params={"limit": 3})
    assert resp.status_code == 200
    data = resp.json()
    assert data["truncated"] is True

    kept = {n["node_id"] for n in data["nodes"]}
    # The entry point's file and its traced callee's file are reserved despite
    # low PageRank; the symbol nodes themselves never enter a file-only export.
    assert "src/api.py" in kept
    assert "src/service.py" in kept
    assert "src/api.py::handler" not in kept
    assert "src/core_0.py" in kept


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


# ---------------------------------------------------------------------------
# Community detail: state, not just shape (Phase 2)
# ---------------------------------------------------------------------------


def _health_metric(path: str, *, score: float, nloc: int) -> dict:
    return {
        "file_path": path,
        "score": score,
        "max_ccn": 3,
        "max_nesting": 2,
        "nloc": nloc,
        "duplication_pct": 0.0,
        "has_test_file": False,
        "line_coverage_pct": None,
        "branch_coverage_pct": None,
        "module": "src",
    }


@pytest.mark.asyncio
async def test_community_detail_rolls_up_health_hot_dead_and_owner(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    await _populate_two_communities(app.state.session_factory, repo["id"])

    async with get_session(app.state.session_factory) as session:
        # a.py: 100 lines at 9.0. b.py: 300 lines at 5.0. LOC-weighted mean is
        # (900 + 1500) / 400 = 6.0, which a plain mean would put at 7.0.
        await crud.save_health_metrics(
            session,
            repo["id"],
            [
                _health_metric("src/a.py", score=9.0, nloc=100),
                _health_metric("src/b.py", score=5.0, nloc=300),
            ],
        )
        await crud.upsert_git_metadata(
            session,
            repository_id=repo["id"],
            file_path="src/a.py",
            is_hotspot=True,
            primary_owner_name="Ada",
        )
        await crud.upsert_git_metadata(
            session,
            repository_id=repo["id"],
            file_path="src/b.py",
            is_hotspot=False,
            primary_owner_name="Ada",
        )
        session.add(
            DeadCodeFinding(
                repository_id=repo["id"],
                file_path="src/b.py",
                kind="unreachable_file",
                status="open",
                confidence=0.9,
            )
        )
        session.add(
            DecisionRecord(
                repository_id=repo["id"],
                title="Adopt FastAPI",
                status="active",
                source="cli",
                affected_files_json=json.dumps(["src/a.py"]),
            )
        )
        await session.flush()

    resp = await client.get(f"/api/graph/{repo['id']}/communities/0")
    assert resp.status_code == 200
    data = resp.json()

    assert data["health_score"] == 6.0
    assert data["scored_member_count"] == 2
    assert data["hot_count"] == 1
    assert data["dead_count"] == 1
    assert data["decision_count"] == 1
    assert data["primary_owner"] == "Ada"
    assert data["primary_owner_file_count"] == 2

    # The flags are on the members too, so the panel can name the files behind
    # each count rather than only reporting a number.
    by_path = {m["path"]: m for m in data["members"]}
    assert by_path["src/a.py"]["is_hotspot"] is True
    assert by_path["src/a.py"]["is_dead"] is False
    assert by_path["src/b.py"]["is_dead"] is True


@pytest.mark.asyncio
async def test_community_detail_health_is_null_when_nothing_is_scored(
    client: AsyncClient, app
) -> None:
    # Not zero. An unscored area has no reading, and a 0.0 would render as the
    # worst possible score on a surface whose whole job is "is this in trouble".
    repo = await create_test_repo(client)
    await _populate_two_communities(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/graph/{repo['id']}/communities/0")
    assert resp.status_code == 200
    data = resp.json()
    assert data["health_score"] is None
    assert data["scored_member_count"] == 0
    assert data["hot_count"] == 0
    assert data["primary_owner"] is None
