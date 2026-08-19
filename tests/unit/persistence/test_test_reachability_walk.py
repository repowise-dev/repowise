"""The attributed reverse walk: which tests reach each changed file.

Runs against real ``graph_nodes`` / ``graph_edges`` rows rather than a stub,
because the walk's whole job is reading those two tables correctly - the edge
type filter, the depth bound, and the per-seed attribution that lets one walk
serve every file in a diff.

``tests_reaching`` is imported under an alias: pytest collects module-level
names matching ``test*``, and the unaliased import would be collected as a test
and error on its missing arguments.
"""

from __future__ import annotations

from repowise.core.analysis.test_reachability import tests_reaching as reaching
from repowise.core.persistence.models import GraphEdge, GraphNode
from tests.unit.persistence.helpers import insert_repo


async def _seed(session, repo_id, *, nodes, edges):
    for path, is_test in nodes.items():
        session.add(
            GraphNode(
                repository_id=repo_id, node_id=path, node_type="file", is_test=is_test
            )
        )
    for src, dst, etype in edges:
        session.add(
            GraphEdge(
                repository_id=repo_id,
                source_node_id=src,
                target_node_id=dst,
                edge_type=etype,
            )
        )
    await session.flush()


async def test_names_the_tests_that_import_a_changed_file(async_session):
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={"tests/test_a.py": True, "tests/test_z.py": True, "src/a.py": False},
        edges=[
            ("tests/test_a.py", "src/a.py", "imports"),
            ("tests/test_z.py", "src/z.py", "imports"),
        ],
    )
    assert await reaching(async_session, repo.id, ["src/a.py"]) == {
        "src/a.py": ["tests/test_a.py"]
    }


async def test_finds_a_behaviour_named_test_two_hops_out(async_session):
    """Opt-in second hop, for repos whose tests go through a package facade."""
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={"tests/test_round_trips.py": True, "src/api.py": False, "src/parser.py": False},
        edges=[
            ("tests/test_round_trips.py", "src/api.py", "imports"),
            ("src/api.py", "src/parser.py", "imports"),
        ],
    )
    result = await reaching(async_session, repo.id, ["src/parser.py"], max_depth=2)
    assert result == {"src/parser.py": ["tests/test_round_trips.py"]}

    # The default stops at one hop, so the facade case is not claimed by default.
    assert await reaching(async_session, repo.id, ["src/parser.py"]) == {}


async def test_attribution_is_per_changed_file(async_session):
    """One walk, several seeds: each test lands against the file it reaches."""
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={
            "tests/test_a.py": True,
            "tests/test_b.py": True,
            "tests/test_both.py": True,
            "src/a.py": False,
            "src/b.py": False,
        },
        edges=[
            ("tests/test_a.py", "src/a.py", "imports"),
            ("tests/test_b.py", "src/b.py", "imports"),
            ("tests/test_both.py", "src/a.py", "imports"),
            ("tests/test_both.py", "src/b.py", "imports"),
        ],
    )
    assert await reaching(async_session, repo.id, ["src/a.py", "src/b.py"]) == {
        "src/a.py": ["tests/test_a.py", "tests/test_both.py"],
        "src/b.py": ["tests/test_b.py", "tests/test_both.py"],
    }


async def test_a_test_file_is_a_leaf(async_session):
    """A shared test helper must not drag its other importers' targets in."""
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={
            "tests/helpers.py": True,
            "tests/test_unrelated.py": True,
            "src/a.py": False,
        },
        edges=[
            ("tests/helpers.py", "src/a.py", "imports"),
            # test_unrelated imports the helper, not src/a.py.
            ("tests/test_unrelated.py", "tests/helpers.py", "imports"),
        ],
    )
    result = await reaching(async_session, repo.id, ["src/a.py"])
    assert result == {"src/a.py": ["tests/helpers.py"]}


async def test_co_change_edges_are_not_reachability(async_session):
    """Files that change together are not files that test each other."""
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={"tests/test_a.py": True, "src/a.py": False},
        edges=[("tests/test_a.py", "src/a.py", "co_changes")],
    )
    assert await reaching(async_session, repo.id, ["src/a.py"]) == {}


async def test_unreached_file_is_absent_not_empty(async_session):
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={"tests/test_a.py": True, "src/a.py": False, "src/lonely.py": False},
        edges=[("tests/test_a.py", "src/a.py", "imports")],
    )
    result = await reaching(async_session, repo.id, ["src/a.py", "src/lonely.py"])
    assert "src/lonely.py" not in result


async def test_repo_with_no_test_nodes_returns_empty(async_session):
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={"src/a.py": False, "src/b.py": False},
        edges=[("src/b.py", "src/a.py", "imports")],
    )
    assert await reaching(async_session, repo.id, ["src/a.py"]) == {}
