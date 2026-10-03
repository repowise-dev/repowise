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

from repowise.core.analysis.test_reachability import (
    ReachDistance,
    call_graph_from_db,
    call_graph_from_graph,
    imported_names_by_test,
    reach_into_symbols,
)
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


async def test_symbol_reach_keeps_the_fewest_hops_and_counts_direct_callers(async_session):
    """Two test functions call the symbol directly; another test only reaches it
    through a facade. The direct test is one hop away with two callers."""
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={"tests/test_walk.py": True, "tests/test_api.py": True, "src/walk.py": False},
        edges=[
            ("tests/test_walk.py", "tests/test_walk.py::test_one", "defines"),
            ("tests/test_walk.py", "tests/test_walk.py::test_two", "defines"),
            ("tests/test_api.py", "tests/test_api.py::test_it", "defines"),
            ("src/walk.py", "src/walk.py::walk", "defines"),
            ("src/walk.py", "src/walk.py::facade", "defines"),
            ("tests/test_walk.py::test_one", "src/walk.py::walk", "calls"),
            ("tests/test_walk.py::test_two", "src/walk.py::walk", "calls"),
            ("tests/test_api.py::test_it", "src/walk.py::facade", "calls"),
            ("src/walk.py::facade", "src/walk.py::walk", "calls"),
        ],
    )
    found = await reach_into_symbols(
        async_session, repo.id, ["src/walk.py::walk"], {"tests/test_walk.py", "tests/test_api.py"}
    )
    assert found == {
        "src/walk.py::walk": {
            "tests/test_walk.py": ReachDistance(hops=1, callers=2),
            "tests/test_api.py": ReachDistance(hops=2, callers=1),
        }
    }


async def test_imported_names_are_read_per_test_and_file(async_session):
    repo = await insert_repo(async_session)
    for source, names in (("tests/test_walk.py", '["walk"]'), ("src/other.py", '["walk"]')):
        async_session.add(
            GraphEdge(
                repository_id=repo.id,
                source_node_id=source,
                target_node_id="src/walk.py",
                edge_type="imports",
                imported_names_json=names,
            )
        )
    await async_session.flush()
    found = await imported_names_by_test(
        async_session, repo.id, ["src/walk.py"], {"tests/test_walk.py"}
    )
    # A production importer is not a test, so it never appears.
    assert found == {"src/walk.py": {"tests/test_walk.py": frozenset({"walk"})}}


async def test_a_detailed_page_matches_a_full_hydration(async_session):
    """Paged surfaces rank every plan without symbol evidence and detail only
    the rows they return; those rows must serialize exactly as before."""
    from repowise.core.analysis.health.refactoring.models import RefactoringSuggestion
    from repowise.core.analysis.health.refactoring.recommendations import (
        detail_recommendations,
        hydrate_recommendations,
    )

    repo = await insert_repo(async_session)
    nodes = {"tests/test_bystander.py": True, "tests/test_walk.py": True, "src/walk.py": False}
    for path, is_test in nodes.items():
        async_session.add(
            GraphNode(repository_id=repo.id, node_id=path, node_type="file", is_test=is_test)
        )
    for name, start, end in (("walk", 1, 20), ("other", 30, 40)):
        async_session.add(
            GraphNode(
                repository_id=repo.id,
                node_id=f"src/walk.py::{name}",
                node_type="symbol",
                file_path="src/walk.py",
                start_line=start,
                end_line=end,
            )
        )
    await _seed(
        async_session,
        repo.id,
        nodes={},
        edges=[
            ("src/walk.py", "src/walk.py::walk", "defines"),
            ("src/walk.py", "src/walk.py::other", "defines"),
            ("tests/test_walk.py", "tests/test_walk.py::test_it", "defines"),
            ("tests/test_bystander.py", "tests/test_bystander.py::test_it", "defines"),
            ("tests/test_walk.py::test_it", "src/walk.py::walk", "calls"),
            ("tests/test_bystander.py::test_it", "src/walk.py::other", "calls"),
        ],
    )

    def plans():
        return [
            RefactoringSuggestion(
                refactoring_type="extract_method",
                file_path="src/walk.py",
                target_symbol="walk",
                line_start=1,
                line_end=20,
                plan={},
                evidence={},
                impact_delta=1.0,
                effort_bucket="M",
                blast_radius={},
                confidence="high",
                source_biomarker="long_function",
            )
        ]

    full = await hydrate_recommendations(async_session, repo.id, plans())
    ranked = await hydrate_recommendations(async_session, repo.id, plans(), rank_only=True)
    detailed = await detail_recommendations(async_session, repo.id, ranked)

    assert [item.as_dict() for item in detailed] == [item.as_dict() for item in full]
    assert full[0].validation.tests == ["tests/test_walk.py", "tests/test_bystander.py"]
    assert full[0].validation.reasons["tests/test_walk.py"] == "calls walk"
    # The rank pass orders nothing it will not serve.
    assert ranked[0].validation.reasons == {}
    assert ranked[0].rank_score == full[0].rank_score


async def test_a_seed_that_calls_another_seed_does_not_shorten_its_distance(async_session):
    """Walked together, ``load`` (a seed) calls ``parse`` (another seed). A test
    calling ``load`` is two hops from ``parse``, however the batch is ordered."""
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={"tests/test_load.py": True, "src/io.py": False},
        edges=[
            ("tests/test_load.py", "tests/test_load.py::test_it", "defines"),
            ("tests/test_load.py::test_it", "src/io.py::load", "calls"),
            ("src/io.py::load", "src/io.py::parse", "calls"),
        ],
    )
    found = await reach_into_symbols(
        async_session, repo.id, ["src/io.py::load", "src/io.py::parse"], {"tests/test_load.py"}
    )
    assert found["src/io.py::load"] == {"tests/test_load.py": ReachDistance(1, 1)}
    assert found["src/io.py::parse"] == {"tests/test_load.py": ReachDistance(2, 1)}
