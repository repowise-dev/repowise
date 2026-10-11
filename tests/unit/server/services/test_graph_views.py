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
    crud,
    upsert_repository,
)
from repowise.core.persistence.models import GraphEdge
from repowise.server.schemas import (
    ArchitectureGraphResponse,
    CommunitySliceResponse,
)
from repowise.server.services.graph_views import (
    Population,
    build_architecture_graph,
    build_community_slice,
    edge_response,
    neighbour_edge_counts,
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
    # The true visible size, so the banner can say "1 most connected of 2".
    assert payload.member_count == 2
    assert [n.node_id for n in payload.nodes if not n.is_boundary] == ["src/a.py"]


async def _seed_mixed_population(session, tmp_path) -> str:
    """Community 0: two production files and three tests; community 1: one
    production file, one test, one doc; community 2: a test-only pair."""
    repo = await upsert_repository(session, name="mixed", local_path=str(tmp_path))
    nodes = [
        ("src/a.py", 0, 0.9, False),
        ("src/b.py", 0, 0.5, False),
        ("tests/test_a.py", 0, 0.95, True),
        ("tests/test_b.py", 0, 0.4, True),
        ("tests/test_c.py", 0, 0.3, True),
        ("src/c.py", 1, 0.6, False),
        ("tests/test_d.py", 1, 0.2, True),
        ("docs/guide.md", 1, 0.1, False),
        ("tests/fixtures/x.py", 2, 0.1, True),
        ("tests/fixtures/y.py", 2, 0.1, True),
        # An unresolved import, stored as a file row: never a member.
        ("external:pytest", 0, 0.99, False),
    ]
    await batch_upsert_graph_nodes(
        session,
        repo.id,
        [
            {
                "node_id": path,
                "node_type": "file",
                "language": "python",
                "pagerank": pr,
                "community_id": cid,
                "is_test": is_test,
                "community_meta_json": json.dumps(
                    {"label": f"c{cid}", "cohesion": 0.1, "conductance": 0.25}
                ),
            }
            for path, cid, pr, is_test in nodes
        ],
    )
    await batch_upsert_graph_edges(
        session,
        repo.id,
        [
            {"source_node_id": "src/a.py", "target_node_id": "src/b.py"},
            {"source_node_id": "src/a.py", "target_node_id": "src/c.py"},
            {"source_node_id": "tests/test_a.py", "target_node_id": "src/a.py"},
            {"source_node_id": "tests/test_a.py", "target_node_id": "src/c.py"},
            {"source_node_id": "src/b.py", "target_node_id": "tests/test_d.py"},
        ],
    )
    await session.flush()
    return repo.id


@pytest.mark.asyncio
async def test_architecture_graph_counts_production_only_by_default(async_session, tmp_path):
    repo_id = await _seed_mixed_population(async_session, tmp_path)

    view = await build_architecture_graph(async_session, repo_id, min_members=1)

    by_cid = {n.community_id: n for n in view.nodes}
    # The test-only community is not drawn at all.
    assert set(by_cid) == {0, 1}
    assert by_cid[0].member_count == 2
    assert by_cid[0].hidden_member_count == 3
    # The top file is the top *visible* file, not the higher-ranked test.
    assert by_cid[0].top_file == "src/a.py"
    assert by_cid[0].conductance == 0.25
    assert by_cid[1].member_count == 1
    assert by_cid[1].hidden_member_count == 2
    # Cross edges count only visible endpoints: a->c stays, b->test_d goes.
    assert [(e.source, e.target, e.edge_count) for e in view.edges] == [(0, 1, 1)]
    assert view.population is not None
    assert (view.population.total, view.population.visible) == (10, 3)
    assert (view.population.tests, view.population.docs) == (6, 1)
    assert view.population.include_tests is False


@pytest.mark.asyncio
async def test_architecture_graph_population_flags_change_the_counts(async_session, tmp_path):
    repo_id = await _seed_mixed_population(async_session, tmp_path)

    view = await build_architecture_graph(
        async_session,
        repo_id,
        min_members=1,
        population=Population(include_tests=True, include_docs=True),
    )

    by_cid = {n.community_id: n for n in view.nodes}
    assert set(by_cid) == {0, 1, 2}
    assert by_cid[0].member_count == 5
    assert by_cid[0].hidden_member_count == 0
    assert by_cid[0].top_file == "tests/test_a.py"
    assert by_cid[1].member_count == 3
    assert {(e.source, e.target, e.edge_count) for e in view.edges} == {(0, 1, 3)}
    assert view.population is not None
    assert view.population.visible == 10
    assert view.population.include_tests is True


@pytest.mark.asyncio
async def test_architecture_graph_names_the_unclustered(async_session, tmp_path):
    repo_id = await _seed_mixed_population(async_session, tmp_path)

    view = await build_architecture_graph(async_session, repo_id, min_members=2)

    # Community 1 has one visible file, so it is below the cut and unclustered.
    assert [n.community_id for n in view.nodes] == [0]
    assert view.unclustered is not None
    assert view.unclustered.file_count == 1
    assert view.unclustered.files == ["src/c.py"]


@pytest.mark.asyncio
async def test_community_slice_filters_members_and_boundary(async_session, tmp_path):
    repo_id = await _seed_mixed_population(async_session, tmp_path)

    payload = await build_community_slice(async_session, repo_id, community_id=0)

    by_id = {n.node_id: n for n in payload.nodes}
    members = {k for k, n in by_id.items() if not n.is_boundary}
    boundary = {k for k, n in by_id.items() if n.is_boundary}
    assert members == {"src/a.py", "src/b.py"}
    # test_d is a boundary neighbour but a test, so it is filtered like a member.
    assert boundary == {"src/c.py"}
    assert payload.member_count == 2
    assert payload.hidden_member_count == 3

    shown = await build_community_slice(
        async_session, repo_id, community_id=0, population=Population(include_tests=True)
    )
    assert shown.member_count == 5
    assert shown.hidden_member_count == 0
    assert {n.node_id for n in shown.nodes if n.is_boundary} == {"src/c.py", "tests/test_d.py"}


@pytest.mark.asyncio
async def test_neighbour_edge_counts_respect_population(async_session, tmp_path):
    repo_id = await _seed_mixed_population(async_session, tmp_path)
    members = ["src/a.py", "src/b.py", "tests/test_a.py"]

    hidden = await neighbour_edge_counts(async_session, repo_id, 0, members)
    shown = await neighbour_edge_counts(
        async_session, repo_id, 0, members, population=Population(include_tests=True)
    )

    # a->c and test_a->c reach community 1; b->test_d only counts with tests on.
    assert hidden == [(1, 2)]
    assert shown == [(1, 3)]


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
