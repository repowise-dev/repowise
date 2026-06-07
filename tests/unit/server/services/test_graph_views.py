"""Direct unit tests for the community-view service builders.

The HTTP endpoints are thin wrappers over these functions; the router tests
in tests/unit/server/test_graph.py pin the wire shapes, while these exercise
the builders without FastAPI — the contract non-HTTP consumers (artifact
precomputation) rely on.
"""

from __future__ import annotations

import json

import pytest

from repowise.core.persistence import (
    batch_upsert_graph_edges,
    batch_upsert_graph_nodes,
    upsert_repository,
)
from repowise.core.persistence import crud
from repowise.core.persistence.models import GraphEdge
from repowise.server.schemas import (
    ArchitectureGraphResponse,
    CommunitySliceResponse,
)
from repowise.server.services.graph_views import (
    build_architecture_graph,
    build_community_slice,
    edge_response,
)


async def _seed_two_communities(session, tmp_path) -> str:
    """Two members in community 0, one in community 1, with a cross edge."""
    repo = await upsert_repository(session, name="demo", local_path=str(tmp_path))
    await batch_upsert_graph_nodes(
        session,
        repo.id,
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
    await batch_upsert_graph_edges(
        session,
        repo.id,
        [
            # Intra-community (0)
            {"source_node_id": "src/a.py", "target_node_id": "src/b.py"},
            # Cross-community (0 -> 1): pulls c.py in as a boundary stub
            {"source_node_id": "src/b.py", "target_node_id": "src/c.py"},
        ],
    )
    await session.flush()
    return repo.id


@pytest.mark.asyncio
async def test_build_architecture_graph_groups_and_edges(async_session, tmp_path):
    repo_id = await _seed_two_communities(async_session, tmp_path)

    view = await build_architecture_graph(async_session, repo_id, min_members=1)

    assert isinstance(view, ArchitectureGraphResponse)
    by_cid = {n.community_id: n for n in view.nodes}
    assert set(by_cid) == {0, 1}
    assert by_cid[0].member_count == 2
    assert by_cid[0].top_file == "src/a.py"  # highest pagerank member
    assert by_cid[1].member_count == 1
    assert "python" in by_cid[0].languages
    # One underlying edge crosses the 0 -> 1 boundary
    assert [(e.source, e.target, e.edge_count) for e in view.edges] == [(0, 1, 1)]
    # Sorted by member count, biggest community first
    assert view.nodes[0].community_id == 0


@pytest.mark.asyncio
async def test_build_architecture_graph_min_members_filter(async_session, tmp_path):
    repo_id = await _seed_two_communities(async_session, tmp_path)

    view = await build_architecture_graph(async_session, repo_id, min_members=2)

    assert [n.community_id for n in view.nodes] == [0]
    # Edges into dropped communities are collapsed away
    assert view.edges == []


@pytest.mark.asyncio
async def test_build_architecture_graph_empty_repo(async_session, tmp_path):
    repo = await upsert_repository(async_session, name="empty", local_path=str(tmp_path))

    view = await build_architecture_graph(async_session, repo.id)

    assert view.nodes == []
    assert view.edges == []


@pytest.mark.asyncio
async def test_build_architecture_graph_aggregates_signals(async_session, tmp_path):
    repo_id = await _seed_two_communities(async_session, tmp_path)
    await crud.upsert_git_metadata(
        async_session,
        repository_id=repo_id,
        file_path="src/a.py",
        is_hotspot=True,
        churn_percentile=0.95,
        primary_owner_name="Alice",
        commit_count_30d=10,
        commit_count_90d=20,
    )
    await async_session.flush()

    view = await build_architecture_graph(async_session, repo_id, min_members=1)

    by_cid = {n.community_id: n for n in view.nodes}
    assert by_cid[0].hotspot_count == 1
    assert by_cid[1].hotspot_count == 0


@pytest.mark.asyncio
async def test_build_community_slice_members_and_boundary(async_session, tmp_path):
    repo_id = await _seed_two_communities(async_session, tmp_path)

    payload = await build_community_slice(async_session, repo_id, community_id=0)

    assert isinstance(payload, CommunitySliceResponse)
    assert payload.community_id == 0
    assert payload.member_count == 2
    assert payload.truncated is False
    by_id = {n.node_id: n for n in payload.nodes}
    assert set(by_id) == {"src/a.py", "src/b.py", "src/c.py"}
    assert by_id["src/a.py"].is_boundary is False
    assert by_id["src/b.py"].is_boundary is False
    assert by_id["src/c.py"].is_boundary is True  # outside neighbor stub
    links = {(link.source, link.target) for link in payload.links}
    assert links == {("src/a.py", "src/b.py"), ("src/b.py", "src/c.py")}


@pytest.mark.asyncio
async def test_build_community_slice_member_limit_truncates(async_session, tmp_path):
    repo_id = await _seed_two_communities(async_session, tmp_path)

    payload = await build_community_slice(
        async_session, repo_id, community_id=0, member_limit=1
    )

    assert payload.truncated is True
    assert payload.member_count == 1


@pytest.mark.asyncio
async def test_build_community_slice_empty_community(async_session, tmp_path):
    repo_id = await _seed_two_communities(async_session, tmp_path)

    payload = await build_community_slice(async_session, repo_id, community_id=999)

    assert payload.nodes == []
    assert payload.links == []
    assert payload.member_count == 0
    assert payload.truncated is False


def test_edge_response_parses_imported_names():
    e = GraphEdge(
        repository_id="r",
        source_node_id="a.py",
        target_node_id="b.py",
        imported_names_json=json.dumps(["helper"]),
    )
    resp = edge_response(e)
    assert resp.source == "a.py"
    assert resp.target == "b.py"
    assert resp.imported_names == ["helper"]


def test_edge_response_tolerates_bad_json():
    e = GraphEdge(
        repository_id="r",
        source_node_id="a.py",
        target_node_id="b.py",
        imported_names_json="{not json",
    )
    assert edge_response(e).imported_names == []
