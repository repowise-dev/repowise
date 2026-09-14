"""The attributed reverse walk: which tests reach each changed file.

Runs against real ``graph_nodes`` / ``graph_edges`` rows rather than a stub,
because the walk's whole job is reading those two tables correctly - the edge
type filter, the ``resolution_origin`` filter, the depth bound, the two-tier
fallback, and the per-seed attribution that lets one walk serve every file in a
diff.

``tests_reaching`` is imported under an alias: pytest collects module-level
names matching ``test*``, and the unaliased import would be collected as a test
and error on its missing arguments.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.analysis.test_reachability import call_graph_from_db, call_graph_from_graph
from repowise.core.analysis.test_reachability import tests_reaching as reaching
from repowise.core.analysis.test_reachability import tests_reaching_by_tier as by_tier
from repowise.core.persistence.crud.graph import (
    batch_upsert_graph_edges,
    get_all_graph_edges,
)
from repowise.core.persistence.models import GraphEdge, GraphNode
from tests.unit.persistence.helpers import insert_repo


async def _seed(session, repo_id, *, nodes, edges):
    """Seed file nodes and edges. An edge is ``(src, dst, type[, origin])``."""
    for path, is_test in nodes.items():
        session.add(
            GraphNode(repository_id=repo_id, node_id=path, node_type="file", is_test=is_test)
        )
    # Deduped: two ``_calls`` into the same source file both declare its
    # symbol, and graph_edges is unique on (repo, src, dst, type).
    for src, dst, etype, *origin in dict.fromkeys(
        (e[0], e[1], e[2], e[3] if len(e) > 3 else None) for e in edges
    ):
        session.add(
            GraphEdge(
                repository_id=repo_id,
                source_node_id=src,
                target_node_id=dst,
                edge_type=etype,
                resolution_origin=origin[0] if origin else None,
            )
        )
    await session.flush()


def _calls(test_file, source_file, *, origin=None):
    """The three edges that put one call from a test into a source file.

    A file is joined to its symbols by ``defines`` and symbols to each other by
    ``calls``, so the shortest real path from a test file to a source file is
    two containment edges bridging one call edge.
    """
    return [
        (test_file, f"{test_file}::test_it", "defines"),
        (source_file, f"{source_file}::run", "defines"),
        (f"{test_file}::test_it", f"{source_file}::run", "calls", origin),
    ]


async def test_names_the_tests_that_call_a_changed_file(async_session):
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={"tests/test_a.py": True, "tests/test_z.py": True, "src/a.py": False},
        edges=[*_calls("tests/test_a.py", "src/a.py")],
    )
    assert await reaching(async_session, repo.id, ["src/a.py"]) == {"src/a.py": ["tests/test_a.py"]}


async def test_transitive_execution_is_found(async_session):
    """The whole reason for the call graph: a test two calls away from the file.

    The import graph structurally cannot see this - the test imports the facade,
    not the parser - and a deeper import hop was measured to add more wrong
    claims than right ones.
    """
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={"tests/test_round_trips.py": True, "src/api.py": False, "src/parser.py": False},
        edges=[
            ("tests/test_round_trips.py", "tests/test_round_trips.py::test_it", "defines"),
            ("src/api.py", "src/api.py::load", "defines"),
            ("src/parser.py", "src/parser.py::parse", "defines"),
            ("tests/test_round_trips.py::test_it", "src/api.py::load", "calls"),
            ("src/api.py::load", "src/parser.py::parse", "calls"),
        ],
    )
    assert await reaching(async_session, repo.id, ["src/parser.py"]) == {
        "src/parser.py": ["tests/test_round_trips.py"]
    }
    # One hop stops at the facade, so the depth bound is real.
    assert await reaching(async_session, repo.id, ["src/parser.py"], call_depth=1) == {}


async def test_name_only_call_resolutions_are_not_evidence(async_session):
    """``global_unique`` matched a name repo-wide, which is a guess, not an edge
    anyone should be sent to run a test on."""
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={"tests/test_a.py": True, "src/a.py": False},
        edges=[*_calls("tests/test_a.py", "src/a.py", origin="global_unique")],
    )
    assert await reaching(async_session, repo.id, ["src/a.py"]) == {}


async def test_the_import_tier_answers_only_where_calls_are_silent(async_session):
    """Fallback, not union: measured at 97.5% precision either way, so the
    weaker tier is free as long as it never speaks over the stronger one."""
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={
            "tests/test_called.py": True,
            "tests/test_imports_only.py": True,
            "src/called.py": False,
            "src/imported.py": False,
        },
        edges=[
            *_calls("tests/test_called.py", "src/called.py"),
            # Also imports the file it calls: the import tier must not add
            # itself on top of the call tier's answer.
            ("tests/test_imports_only.py", "src/called.py", "imports"),
            ("tests/test_imports_only.py", "src/imported.py", "imports"),
        ],
    )
    result = await by_tier(async_session, repo.id, ["src/called.py", "src/imported.py"])
    assert result["src/called.py"].tests == ["tests/test_called.py"]
    assert result["src/called.py"].via == "call-graph"
    assert result["src/imported.py"].tests == ["tests/test_imports_only.py"]
    assert result["src/imported.py"].via == "import-graph"

    # Opting the import tier out leaves the file the call graph cannot reach.
    calls_only = await reaching(
        async_session, repo.id, ["src/called.py", "src/imported.py"], import_depth=0
    )
    assert set(calls_only) == {"src/called.py"}


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
            *_calls("tests/test_a.py", "src/a.py"),
            *_calls("tests/test_b.py", "src/b.py"),
            *_calls("tests/test_both.py", "src/a.py"),
            ("tests/test_both.py::test_it", "src/b.py::run", "calls"),
        ],
    )
    assert await reaching(async_session, repo.id, ["src/a.py", "src/b.py"]) == {
        "src/a.py": ["tests/test_a.py", "tests/test_both.py"],
        "src/b.py": ["tests/test_b.py", "tests/test_both.py"],
    }


async def test_a_test_file_is_a_leaf(async_session):
    """A shared test helper must not drag its other callers' targets in."""
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
            *_calls("tests/helpers.py", "src/a.py"),
            ("tests/test_unrelated.py", "tests/test_unrelated.py::test_it", "defines"),
            # test_unrelated calls the helper, not src/a.py.
            ("tests/test_unrelated.py::test_it", "tests/helpers.py::test_it", "calls"),
        ],
    )
    assert await reaching(async_session, repo.id, ["src/a.py"]) == {
        "src/a.py": ["tests/helpers.py"]
    }


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
        edges=[*_calls("tests/test_a.py", "src/a.py")],
    )
    result = await reaching(async_session, repo.id, ["src/a.py", "src/lonely.py"])
    assert "src/lonely.py" not in result


async def test_repo_with_no_test_nodes_returns_empty(async_session):
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={"src/a.py": False, "src/b.py": False},
        edges=[*_calls("src/b.py", "src/a.py")],
    )
    assert await reaching(async_session, repo.id, ["src/a.py"]) == {}


async def test_database_and_memory_builders_apply_the_same_execution_policy(async_session):
    repo = await insert_repo(async_session)
    edges = [
        ("tests/t.py", "tests/t.py::test", "defines"),
        ("tests/t.py::test", "src/a.py::run", "calls", "same_file"),
        ("src/a.py::run", "src/b.py::impl", "dispatches_to"),
        ("tests/t.py::test", "src/guess.py::run", "calls", "global_unique"),
        ("tests/t.py::test", "src/named.py::handler", "references"),
    ]
    await _seed(
        async_session,
        repo.id,
        nodes={"tests/t.py": True, "src/a.py": False, "src/b.py": False},
        edges=edges,
    )
    graph = nx.DiGraph()
    for source, target, edge_type, *origin in edges:
        attrs = {"edge_type": edge_type}
        if origin and origin[0] is not None:
            attrs["resolution_origin"] = origin[0]
        graph.add_edge(source, target, **attrs)

    memory = call_graph_from_graph(graph)
    database = await call_graph_from_db(async_session, repo.id)

    assert database.declares == memory.declares
    assert database.forward == memory.forward
    assert database.reverse == memory.reverse


async def test_call_site_lines_round_trip_through_edge_persistence(async_session):
    repo = await insert_repo(async_session)
    edge = {
        "source_node_id": "a.py::run",
        "target_node_id": "b.py::load",
        "edge_type": "calls",
        "call_lines_json": "[4, 9]",
    }

    await batch_upsert_graph_edges(async_session, repo.id, [edge])
    rows = await get_all_graph_edges(async_session, repo.id)

    assert rows[0]["call_lines"] == [4, 9]
