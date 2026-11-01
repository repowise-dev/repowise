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
    direct_dependents,
    direct_importers,
    imported_names_by_test,
    reach_into_symbols,
)
from repowise.core.analysis.test_reachability import tests_reaching as reaching
from repowise.core.analysis.test_reachability import tests_reaching_by_tier as by_tier
from repowise.core.persistence.crud.graph import (
    batch_upsert_graph_edges,
    get_all_graph_edges,
)
from repowise.core.persistence.models import GraphEdge, GraphMetric, GraphNode
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


async def test_hydration_orders_a_plans_tests_by_the_symbol_they_call(async_session):
    """A test calling the changed symbol leads one that calls only a neighbour."""
    from repowise.core.analysis.health.refactoring.models import RefactoringSuggestion
    from repowise.core.persistence.crud.analysis.refactoring_recommendations import (
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

    assert full[0].validation.tests == ["tests/test_walk.py", "tests/test_bystander.py"]
    assert full[0].validation.reasons["tests/test_walk.py"] == "calls walk"


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


async def _fan_in(session, repo_id, fan_in):
    for path, degree in fan_in.items():
        session.add(GraphMetric(repository_id=repo_id, node_id=path, in_degree=degree))
    await session.flush()


def _via(test, middle, target):
    return [
        (test, f"{test}::test_it", "defines"),
        (middle, f"{middle}::m", "defines"),
        (target, f"{target}::run", "defines"),
        (f"{test}::test_it", f"{middle}::m", "calls"),
        (f"{middle}::m", f"{target}::run", "calls"),
    ]


async def test_a_test_reaching_a_file_only_through_a_hub_is_dropped(async_session):
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={
            "tests/test_app.py": True,
            "tests/test_helper.py": True,
            "src/app.py": False,
            "src/helper.py": False,
            "src/a.py": False,
        },
        edges=[
            *_via("tests/test_app.py", "src/app.py", "src/a.py"),
            *_via("tests/test_helper.py", "src/helper.py", "src/a.py"),
        ],
    )
    both = await by_tier(async_session, repo.id, ["src/a.py"])
    assert set(both["src/a.py"].tests) == {"tests/test_app.py", "tests/test_helper.py"}
    gated = await by_tier(async_session, repo.id, ["src/a.py"], avoid={"src/app.py"})
    assert gated["src/a.py"].tests == ["tests/test_helper.py"]
    assert gated["src/a.py"].total == 1


async def test_a_hub_still_carries_reach_into_its_own_symbols(async_session):
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={"tests/test_hub.py": True, "src/hub.py": False},
        edges=[
            ("tests/test_hub.py", "tests/test_hub.py::test_it", "defines"),
            ("tests/test_hub.py::test_it", "src/hub.py::outer", "calls"),
            ("src/hub.py::outer", "src/hub.py::inner", "calls"),
        ],
    )
    found = await reach_into_symbols(
        async_session, repo.id, ["src/hub.py::inner"], {"tests/test_hub.py"}, avoid={"src/hub.py"}
    )
    assert found == {"src/hub.py::inner": {"tests/test_hub.py": ReachDistance(2, 1)}}


async def test_a_test_importing_a_file_through_a_barrel_imports_it(async_session):
    repo = await insert_repo(async_session)
    imports = [
        ("src/index.ts", "src/util.ts", '["walk", "other"]'),
        ("src/all.ts", "src/util.ts", '["*"]'),
        ("tests/a.test.ts", "src/index.ts", '["walk"]'),
        ("tests/b.test.ts", "src/index.ts", '["unrelated"]'),
        ("tests/c.test.ts", "src/index.ts", "[]"),
        ("tests/d.test.ts", "src/all.ts", '["lonely"]'),
    ]
    for source, target, names in imports:
        async_session.add(
            GraphEdge(
                repository_id=repo.id,
                source_node_id=source,
                target_node_id=target,
                edge_type="imports",
                imported_names_json=names,
            )
        )
    await async_session.flush()
    tests = {"tests/a.test.ts", "tests/b.test.ts", "tests/c.test.ts", "tests/d.test.ts"}
    # ``all.ts`` is no barrel by name, so ``d`` is not followed through it.
    assert await imported_names_by_test(async_session, repo.id, ["src/util.ts"], tests) == {
        "src/util.ts": {"tests/a.test.ts": frozenset({"walk"}), "tests/c.test.ts": frozenset()}
    }


async def _hub_repo(session, *, tests=(), edges=()):
    """``src/util.py`` is a hub (fan-in 60) with three functions: ``test_runner``
    calls ``walk``, ``test_bystander`` calls ``other``, nothing calls ``lonely``."""
    repo = await insert_repo(session)
    nodes = {"tests/test_bystander.py": True, "tests/test_runner.py": True, "src/util.py": False}
    nodes |= dict.fromkeys(tests, True)
    for path, is_test in nodes.items():
        session.add(
            GraphNode(repository_id=repo.id, node_id=path, node_type="file", is_test=is_test)
        )
    for name, start, end in (("walk", 1, 20), ("other", 30, 40), ("lonely", 50, 60)):
        session.add(
            GraphNode(
                repository_id=repo.id,
                node_id=f"src/util.py::{name}",
                node_type="symbol",
                file_path="src/util.py",
                start_line=start,
                end_line=end,
            )
        )
    await _seed(
        session,
        repo.id,
        nodes={},
        edges=[
            ("src/util.py", "src/util.py::walk", "defines"),
            ("src/util.py", "src/util.py::other", "defines"),
            ("src/util.py", "src/util.py::lonely", "defines"),
            ("tests/test_runner.py", "tests/test_runner.py::test_it", "defines"),
            ("tests/test_bystander.py", "tests/test_bystander.py::test_it", "defines"),
            ("tests/test_runner.py::test_it", "src/util.py::walk", "calls"),
            ("tests/test_bystander.py::test_it", "src/util.py::other", "calls"),
        ],
    )
    for source, target, names in edges:
        session.add(
            GraphEdge(
                repository_id=repo.id,
                source_node_id=source,
                target_node_id=target,
                edge_type="imports",
                imported_names_json=names,
            )
        )
    session.add(GraphMetric(repository_id=repo.id, node_id="src/util.py", in_degree=60))
    await session.flush()
    return repo


async def _util_validations(session, repo_id):
    from repowise.core.analysis.health.refactoring.models import RefactoringSuggestion
    from repowise.core.persistence.crud.analysis.refactoring_recommendations import (
        hydrate_recommendations,
    )

    plans = [
        RefactoringSuggestion(
            refactoring_type="extract_method",
            file_path="src/util.py",
            target_symbol=symbol,
            line_start=start,
            line_end=end,
            plan={},
            evidence={},
            impact_delta=1.0,
            effort_bucket="M",
            blast_radius={},
            confidence="high",
            source_biomarker="long_function",
        )
        for symbol, start, end in (("walk", 1, 20), ("lonely", 50, 60))
    ]
    items = await hydrate_recommendations(session, repo_id, plans)
    return {item.suggestion.target_symbol: item for item in items}


async def test_a_hub_plan_lists_only_tests_reaching_the_changed_symbol(async_session):
    """Most of a suite reaches a hub through some other symbol of it; only reach
    into the symbol a plan changes validates the plan. With none, the plan has no
    command and asks for a characterization test first."""
    repo = await _hub_repo(async_session)
    full = await _util_validations(async_session, repo.id)
    walked, lonely = full["walk"].validation, full["lonely"].validation
    assert walked.tests == ["tests/test_runner.py"]
    assert walked.total == 1
    assert walked.prerequisite is None
    assert lonely.basis == "unknown"
    assert lonely.tests == []
    assert lonely.commands == []
    assert lonely.prerequisite == (
        "No test reaches `lonely`; add a characterization test before the edit."
    )


async def test_a_hub_import_the_graph_cannot_tie_to_a_symbol_still_counts(async_session):
    """``import util`` then ``util.lonely()`` with the call unresolved, a namespace
    import, or ``lonely`` through a barrel: none proves the test misses the
    change, so none is dropped into "no test reaches this". An import of other
    names is proof enough."""
    repo = await _hub_repo(
        async_session,
        tests=(
            "tests/test_module.py",
            "tests/test_star.py",
            "tests/test_barrel.py",
            "tests/test_named.py",
            "tests/test_util_extra.py",
        ),
        edges=(
            ("tests/test_module.py", "src/util.py", '["util"]'),
            ("tests/test_star.py", "src/util.py", '["*"]'),
            ("src/__init__.py", "src/util.py", '["lonely"]'),
            ("tests/test_barrel.py", "src/__init__.py", '["lonely"]'),
            ("tests/test_named.py", "src/util.py", '["other"]'),
            # Named like a test of the file, but no exemption from the hub filter.
            ("tests/test_util_extra.py", "src/util.py", '["other"]'),
        ),
    )
    lonely = (await _util_validations(async_session, repo.id))["lonely"].validation
    assert lonely.basis == "inferred"
    assert set(lonely.tests) == {
        "tests/test_module.py",
        "tests/test_star.py",
        "tests/test_barrel.py",
    }
    assert lonely.prerequisite is None


async def test_the_import_walk_from_memory_matches_the_walk_from_the_database(async_session):
    """One bulk read answers the levels the per-level queries do, at any depth."""
    import sys

    from repowise.core.analysis.test_reachability import load_import_graph

    repo = await insert_repo(async_session)
    files = ["src/a.py", "src/b.py", "src/c.py", "src/d.py", "src/e.py"]
    tests = ["tests/test_a.py", "tests/test_d.py", "tests/helper.py", "tests/test_h.py"]
    await _seed(
        async_session,
        repo.id,
        nodes={**dict.fromkeys(files, False), **dict.fromkeys(tests, True)},
        edges=[
            # A diamond (d reaches a through b and c), a cycle (b <-> e) and a
            # test importing a test helper.
            ("src/b.py", "src/a.py", "imports"),
            ("src/c.py", "src/a.py", "imports"),
            ("src/d.py", "src/b.py", "imports"),
            ("src/d.py", "src/c.py", "type_use"),
            ("src/e.py", "src/b.py", "imports"),
            ("src/b.py", "src/e.py", "imports"),
            ("tests/test_a.py", "src/a.py", "imports"),
            ("tests/test_d.py", "src/d.py", "imports"),
            ("tests/helper.py", "src/e.py", "imports"),
            ("tests/test_h.py", "tests/helper.py", "imports"),
        ],
    )
    graph = await load_import_graph(async_session, repo.id)
    test_files = set(tests)
    for depth in (1, 2, sys.maxsize):
        args = dict(call_depth=0, import_depth=depth, test_files=test_files)
        stored = await by_tier(async_session, repo.id, ["src/a.py", "src/c.py"], **args)
        held = await by_tier(
            async_session, repo.id, ["src/a.py", "src/c.py"], import_graph=graph, **args
        )
        assert held == stored
    assert graph.hops(["src/a.py"]) == {
        "src/a.py": 0,
        "src/b.py": 1,
        "src/c.py": 1,
        "tests/test_a.py": 1,
        "src/d.py": 2,
        "src/e.py": 2,
        "tests/test_d.py": 3,
        "tests/helper.py": 3,
        "tests/test_h.py": 4,
    }


async def test_the_import_graphs_and_placement_are_read_once_per_index_state(
    async_session, monkeypatch
):
    """Each selection asks for them; a process serving many reads them once."""
    from repowise.core.analysis.test_reach import db

    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={"src/pkg/__init__.py": False, "src/pkg/a.py": False, "tests/test_a.py": True},
        edges=[("tests/test_a.py", "src/pkg/a.py", "imports")],
    )
    reads: list[str] = []
    real_graph, real_placed = db.load_import_graph, db.placed_test_files

    async def graph(session, repo_id):
        reads.append("graph")
        return await real_graph(session, repo_id)

    async def placed(session, repo_id):
        reads.append("placed")
        return await real_placed(session, repo_id)

    monkeypatch.setattr(db, "load_import_graph", graph)
    monkeypatch.setattr(db, "placed_test_files", placed)
    first = await db.cached_import_graphs(async_session, repo.id)
    assert await db.cached_import_graphs(async_session, repo.id) is first
    assert first[1] == first[0].with_package_inits()
    placement = await db.cached_placement(async_session, repo.id)
    assert placement[0] == {"tests/test_a.py"}
    assert await db.cached_placement(async_session, repo.id) == placement
    assert reads == ["graph", "placed"]


_KY_LIKE = {
    "src/core/Ky.ts": "export class Ky {\n  static create(input: string) { return new Ky(); }\n}\n",
    # Three calls deep before the test's own call: past the call walk's depth.
    "src/index.ts": (
        "import {Ky} from './core/Ky';\n"
        "const createRequest = (input: string) => Ky.create(input);\n"
        "const createInstance = () => (input: string) => createRequest(input);\n"
        "const ky = createInstance();\n"
        "export default ky;\n"
        "export type {Hooks} from './types/hooks';\n"
    ),
    # A type import back into the package entry: index.ts is not test-only.
    "src/types/hooks.ts": "import type ky from '../index';\nexport type Hooks = typeof ky;\n",
    "test/x.test.ts": (
        "import ky from '../src/index';\nimport {pool} from '../src/pool';\n"
        "ky('https://example.com');\n"
    ),
    # Source imports pool.ts too and it is no barrel, so the helper hop stops there.
    "src/pool.ts": "import {retry} from './retry';\nexport const pool = [retry];\n",
    "src/retry.ts": "export const retry = 1;\n",
    "src/agent.ts": "import {pool} from './pool';\nexport const agent = pool;\n",
}


async def test_any_reach_passes_through_the_package_entry_tests_import(tmp_path, async_session):
    """A test importing a package's ``index.ts`` reaches the files it imports."""
    from repowise.core.analysis.test_reachability import (
        any_reached,
        any_tests_reaching,
        clear_test_map_cache,
    )
    from repowise.core.persistence.crud.graph import batch_upsert_graph_edges
    from repowise.core.pipeline.persist import _edge_row, persist_graph_nodes
    from tests.unit.analysis.test_public_api_flag import build_fixture

    builder = build_fixture(tmp_path, _KY_LIKE)
    repo = await insert_repo(async_session)
    await persist_graph_nodes(async_session, repo.id, builder, ep_scores={})
    edges = [_edge_row(u, v, d) for u, v, d in builder.graph().edges(data=True)]
    await batch_upsert_graph_edges(async_session, repo.id, edges)
    clear_test_map_cache()

    found = await any_tests_reaching(async_session, repo.id, ["src/core/Ky.ts", "src/retry.ts"])
    assert found["src/core/Ky.ts"].reached.tests == ["test/x.test.ts"]
    assert found["src/core/Ky.ts"].reached.via == "import-graph"
    # The helper hop stops at pool.ts (source imports it too), but the package
    # walk does not: pool.ts sits beside retry.ts, and a test importing it runs retry.ts.
    assert found["src/retry.ts"].reached.tests == ["test/x.test.ts"]
    assert found["src/retry.ts"].reached.via == "import-graph"

    rank_free = await any_reached(async_session, repo.id, ["src/core/Ky.ts", "src/retry.ts"])
    assert rank_free.keys() == found.keys()


_SWAPPED = {
    "app/__init__.py": "",
    "app/core.py": "from app import util\n\ndef run():\n    return util.helper()\n",
    "app/util.py": "def helper():\n    return 1\n",
    "tests/test_core.py": (
        "from unittest.mock import patch\n\nfrom app.core import run\n\n"
        '@patch("app.util")\ndef test_run(m):\n    assert run()\n'
    ),
    "tests/test_util.py": "from app.util import helper\n\ndef test_helper():\n    assert helper()\n",
    "web/a.ts": "export const a = 1;\n",
    "web/a.test.ts": 'import {vi} from "vitest";\nvi.mock("./a", () => ({a: 2}));\n',
}


async def test_any_reach_reads_which_tests_replace_a_module_from_the_checkout(
    tmp_path, async_session
):
    from repowise.core.analysis.test_reachability import (
        any_tests_reaching,
        cached_test_masks,
        clear_test_map_cache,
    )
    from repowise.core.persistence.crud.graph import batch_upsert_graph_edges
    from repowise.core.pipeline.persist import _edge_row, persist_graph_nodes
    from tests.unit.analysis.test_public_api_flag import build_fixture

    both = tmp_path / "both"
    both.mkdir()
    builder = build_fixture(both, _SWAPPED)
    repo = await insert_repo(async_session, local_path=str(both))
    await persist_graph_nodes(async_session, repo.id, builder, ep_scores={})
    edges = [_edge_row(u, v, d) for u, v, d in builder.graph().edges(data=True)]
    await batch_upsert_graph_edges(async_session, repo.id, edges)
    clear_test_map_cache()

    assert await cached_test_masks(async_session, repo.id) == {
        "tests/test_core.py": frozenset({"app/util.py"}),
        "web/a.test.ts": frozenset({"web/a.ts"}),
    }
    found = await any_tests_reaching(async_session, repo.id, ["app/core.py", "app/util.py"])
    assert found["app/core.py"].reached.tests == ["tests/test_core.py"]
    assert found["app/util.py"].reached.tests == ["tests/test_util.py"]

    # Without the other test, util.py is reached by a replacing test alone.
    alone = tmp_path / "alone"
    alone.mkdir()
    builder = build_fixture(alone, {k: v for k, v in _SWAPPED.items() if k != "tests/test_util.py"})
    repo = await insert_repo(async_session, name="swapped-only", local_path=str(alone))
    await persist_graph_nodes(async_session, repo.id, builder, ep_scores={})
    edges = [_edge_row(u, v, d) for u, v, d in builder.graph().edges(data=True)]
    await batch_upsert_graph_edges(async_session, repo.id, edges)
    clear_test_map_cache()
    found = await any_tests_reaching(async_session, repo.id, ["app/util.py"])
    assert found["app/util.py"].state == "mocked_only"
    assert found["app/util.py"].mocked_by == ("tests/test_core.py",)

    # No checkout on disk: no masks, so the test keeps its reach.
    repo = await insert_repo(async_session, name="elsewhere", local_path=str(tmp_path / "gone"))
    await persist_graph_nodes(async_session, repo.id, builder, ep_scores={})
    await batch_upsert_graph_edges(async_session, repo.id, edges)
    clear_test_map_cache()
    found = await any_tests_reaching(async_session, repo.id, ["app/util.py"])
    assert found["app/util.py"].state == "reached"


async def test_stored_test_masks_spare_a_new_process_the_test_file_reads(
    tmp_path, async_session, monkeypatch
):
    """Masks stored at index time serve a new process; a moved index state rebuilds them."""
    import shutil

    from sqlalchemy import text

    import repowise.core.analysis.test_reach.db as tr
    from repowise.core.analysis.test_reachability import (
        cached_test_masks,
        clear_test_map_cache,
        write_test_masks_snapshot,
    )
    from repowise.core.persistence.crud.graph import batch_upsert_graph_edges
    from repowise.core.pipeline.persist import _edge_row, persist_graph_nodes
    from tests.unit.analysis.test_public_api_flag import build_fixture

    root = tmp_path / "repo"
    root.mkdir()
    builder = build_fixture(root, _SWAPPED)
    repo = await insert_repo(async_session, local_path=str(root))
    await persist_graph_nodes(async_session, repo.id, builder, ep_scores={})
    edges = [_edge_row(u, v, d) for u, v, d in builder.graph().edges(data=True)]
    await batch_upsert_graph_edges(async_session, repo.id, edges)
    clear_test_map_cache()
    assert await write_test_masks_snapshot(async_session, repo.id)
    await async_session.commit()
    expected = {
        "tests/test_core.py": frozenset({"app/util.py"}),
        "web/a.test.ts": frozenset({"web/a.ts"}),
    }

    # A new process: nothing cached, and the checkout gone, so only the stored
    # copy can answer.
    clear_test_map_cache()
    shutil.rmtree(root)
    assert await cached_test_masks(async_session, repo.id) == expected

    # A write that leaves the index state alone keeps the stored copy.
    await async_session.execute(
        text("UPDATE graph_nodes SET start_line = 7 WHERE repository_id = :r"), {"r": repo.id}
    )
    await async_session.commit()
    clear_test_map_cache()
    assert await cached_test_masks(async_session, repo.id) == expected

    # A new index state misses it and builds from the checkout (gone: no masks).
    await async_session.execute(
        text("UPDATE repositories SET head_commit = 'moved' WHERE id = :r"), {"r": repo.id}
    )
    await async_session.commit()
    clear_test_map_cache()
    reads: list[int] = []
    real = tr._read_texts
    monkeypatch.setattr(tr, "_read_texts", lambda *a: reads.append(1) or real(*a))
    assert await cached_test_masks(async_session, repo.id) == {}
    assert reads == [1]


async def test_reached_files_are_walked_once_per_index_state(async_session, monkeypatch):
    import repowise.core.analysis.test_reach.db as tr

    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={"tests/test_a.py": True, "src/a.py": False, "src/b.py": False},
        edges=[*_calls("tests/test_a.py", "src/a.py")],
    )
    loads = 0
    real = tr.call_graph_from_db

    async def counting(session, repo_id):
        nonlocal loads
        loads += 1
        return await real(session, repo_id)

    monkeypatch.setattr(tr, "call_graph_from_db", counting)

    first = await tr.cached_files_reached_by_tests(async_session, repo.id)
    second = await tr.cached_files_reached_by_tests(async_session, repo.id)

    assert first == second == frozenset({"src/a.py"})
    assert loads == 1


async def test_reads_over_more_files_than_one_statement_can_bind(async_session):
    """A repository-wide read binds a variable per file or symbol, past what one
    SQLite statement accepts (SQLITE_MAX_VARIABLE_NUMBER, 32766)."""
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={"tests/test_a.py": True, "src/a.py": False, "src/b.py": False},
        edges=[
            ("tests/test_a.py", "src/a.py", "imports"),
            ("src/b.py", "src/a.py", "imports"),
            *_calls("tests/test_a.py", "src/b.py"),
        ],
    )
    files = [f"src/gone_{i}.py" for i in range(40_000)] + ["src/a.py", "src/b.py"]
    assert await direct_importers(async_session, repo.id, files) == {
        "tests/test_a.py",
        "src/b.py",
    }
    assert await direct_dependents(async_session, repo.id, files) == {
        "src/a.py": {"tests/test_a.py", "src/b.py"},
        "src/b.py": {"tests/test_a.py"},
    }
    names = await imported_names_by_test(async_session, repo.id, files, {"tests/test_a.py"})
    assert names == {"src/a.py": {"tests/test_a.py": frozenset()}}


async def test_a_node_listed_twice_across_batches_reads_its_edges_once(async_session):
    """A repeated input landing in two batches must not repeat its pairs: one
    ``IN`` list matches a node once however often it is listed."""
    from repowise.core.analysis.test_reach.db import (
        _IN_BATCH,
        _edges_from,
        _edges_into,
        _imported_names,
    )

    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={"tests/test_a.py": True, "src/a.py": False},
        edges=[("tests/test_a.py", "src/a.py", "imports")],
    )
    padding = [f"src/gone_{i}.py" for i in range(_IN_BATCH)]
    into = await _edges_into(
        async_session, repo.id, ["src/a.py", *padding, "src/a.py"], ["imports"], frozenset()
    )
    assert into == [("tests/test_a.py", "src/a.py")]
    out = await _edges_from(
        async_session, repo.id, ["tests/test_a.py", *padding, "tests/test_a.py"], ["imports"]
    )
    assert out == [("tests/test_a.py", "src/a.py")]
    names = await _imported_names(async_session, repo.id, ["src/a.py", *padding, "src/a.py"])
    assert names == [("tests/test_a.py", "src/a.py", frozenset())]


async def test_the_capped_list_keeps_the_closest_tests_and_a_changed_test(
    async_session, monkeypatch
):
    """Ranked before the cap: a direct caller survives many path-earlier far ones,
    and a test that is itself a target leads."""
    from repowise.core.analysis.test_reach import model as tr

    monkeypatch.setattr(tr, "MAX_TESTS_PER_TARGET", 2)
    far = [f"tests/a/test_far_{i}.py" for i in range(3)]
    edges = [
        ("src/hub.py", "src/hub.py::run", "defines"),
        ("src/mid.py", "src/mid.py::go", "defines"),
        ("src/mid.py::go", "src/hub.py::run", "calls"),
        *_calls("tests/z/test_direct.py", "src/hub.py"),
    ]
    for test in [*far, "tests/z/test_edited.py"]:
        edges += [
            (test, f"{test}::test_it", "defines"),
            (f"{test}::test_it", "src/mid.py::go", "calls"),
        ]
    tests = [*far, "tests/z/test_direct.py", "tests/z/test_edited.py"]
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={**dict.fromkeys(tests, True), "src/hub.py": False, "src/mid.py": False},
        edges=edges,
    )
    found = await by_tier(async_session, repo.id, ["src/hub.py"])
    assert found["src/hub.py"].tests == ["tests/z/test_direct.py", far[0]]
    assert found["src/hub.py"].total == 5

    found = await by_tier(async_session, repo.id, ["src/hub.py", "tests/z/test_edited.py"])
    assert found["src/hub.py"].tests == ["tests/z/test_edited.py", "tests/z/test_direct.py"]


async def test_the_import_tier_keeps_a_direct_importer_before_the_cap(async_session, monkeypatch):
    from repowise.core.analysis.test_reach import model as tr

    monkeypatch.setattr(tr, "MAX_TESTS_PER_TARGET", 1)
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={
            "tests/a/test_via_facade.py": True,
            "tests/z/test_direct.py": True,
            "src/api.py": False,
            "src/core.py": False,
        },
        edges=[
            ("tests/a/test_via_facade.py", "src/api.py", "imports"),
            ("src/api.py", "src/core.py", "imports"),
            ("tests/z/test_direct.py", "src/core.py", "imports"),
        ],
    )
    found = await by_tier(async_session, repo.id, ["src/core.py"], call_depth=0, import_depth=2)
    assert found["src/core.py"].tests == ["tests/z/test_direct.py"]
    assert found["src/core.py"].total == 2


_PARITY = {
    **_KY_LIKE,
    "app/__init__.py": "from .core import run\n",
    "app/core.py": "from .util import helper\n\ndef run():\n    return helper()\n",
    "app/util.py": "def helper():\n    return 1\n",
    "app/deep.py": "from app.util import helper\n\ndef deep():\n    return helper()\n",
    "app/lonely.py": "def alone():\n    return 2\n",
    "tests/test_core.py": "from app.core import run\n\ndef test_run():\n    assert run() == 1\n",
    "tests/test_pkg.py": "import app\n\ndef test_pkg():\n    assert app.run()\n",
}


async def test_the_stored_walk_and_the_in_memory_walk_return_the_same_rows(tmp_path, async_session):
    """One walk, two graph sources: the rows must match for every file."""
    from repowise.core.analysis.test_reachability import (
        build_walk_maps,
        clear_test_map_cache,
        load_test_files,
        reach_states,
        reach_states_from_db,
        reaching_by_tier,
        replaced_files,
    )
    from repowise.core.persistence.crud.graph import batch_upsert_graph_edges
    from repowise.core.pipeline.persist import _edge_row, persist_graph_nodes
    from tests.unit.analysis.test_public_api_flag import build_fixture

    builder = build_fixture(tmp_path, _PARITY)
    repo = await insert_repo(async_session)
    await persist_graph_nodes(async_session, repo.id, builder, ep_scores={})
    edges = [_edge_row(u, v, d) for u, v, d in builder.graph().edges(data=True)]
    await batch_upsert_graph_edges(async_session, repo.id, edges)
    clear_test_map_cache()

    maps = build_walk_maps(builder.graph())
    assert maps.test_files == await load_test_files(async_session, repo.id)
    paths = sorted(_PARITY)
    stored = await by_tier(async_session, repo.id, paths)
    assert stored == reaching_by_tier(maps, paths, helpers=False)
    assert stored  # the fixture exercises the walk at all
    asked = [*paths, "app/added.py", "app/types.py"]
    evidence = {
        "changed_tests": ["tests/test_lonely.py"],
        "contents": {
            "app/types.py": "from typing import TypedDict\nclass R(TypedDict):\n    a: int\n"
        },
    }
    in_memory = reach_states(maps, asked, **evidence)
    assert await reach_states_from_db(async_session, repo.id, asked, **evidence) == in_memory
    assert in_memory["app/lonely.py"].reached.via == "changed-test"
    assert in_memory["app/types.py"].state == "type_only"
    assert {in_memory[p].state for p in in_memory} >= {"reached", "unreached", "new"}

    # Both Python tests swap ``app.util`` out and the TS test swaps ``retry.ts``,
    # so only replacing tests reach either.
    swap = 'from unittest.mock import patch\n\n@patch("app.util")\ndef test_swapped(m): ...\n'
    texts = dict.fromkeys(["tests/test_core.py", "tests/test_pkg.py"], swap)
    texts["test/x.test.ts"] = (
        'import {vi} from "vitest";\nvi.mock("../src/retry", () => ({retry: 1}));\n'
    )
    masks = replaced_files(texts, _PARITY)
    assert masks == {
        **{t: {"app/util.py"} for t in ("tests/test_core.py", "tests/test_pkg.py")},
        "test/x.test.ts": {"src/retry.ts"},
    }
    in_memory = reach_states(maps, asked, masks=masks, **evidence)
    assert await reach_states_from_db(async_session, repo.id, asked, masks=masks, **evidence) == (
        in_memory
    )
    assert in_memory["app/util.py"].state == "mocked_only"
    assert in_memory["src/retry.ts"].state == "mocked_only"
    assert in_memory["app/core.py"].state == "reached"


async def test_stored_call_hubs_match_the_in_memory_ones(async_session):
    """Distinct reliable callers, tests included; a name-guessed call does not count."""
    import networkx as nx

    from repowise.core.analysis.test_reachability import build_walk_maps, cached_hub_files
    from repowise.core.analysis.test_reachability import clear_test_map_cache as clear

    tests = [f"tests/test_{i:02}.py" for i in range(10)]
    edges = [
        ("src/hub.py", "src/hub.py::run", "defines"),
        ("src/few.py", "src/few.py::f", "defines"),
    ]
    for t in tests:
        edges += [(t, f"{t}::t", "defines"), (f"{t}::t", "src/hub.py::run", "calls")]
    edges += [(f"{t}::t", "src/few.py::f", "calls", "global_unique") for t in tests]
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        nodes={**dict.fromkeys(tests, True), "src/hub.py": False, "src/few.py": False},
        edges=edges,
    )
    clear()
    g = nx.MultiDiGraph()
    for t in tests:
        g.add_node(t, node_type="file", is_test=True)
    for src, dst, kind, *origin in edges:
        g.add_edge(src, dst, edge_type=kind, resolution_origin=origin[0] if origin else None)
    stored = await cached_hub_files(async_session, repo.id)
    assert stored == build_walk_maps(g).hubs() == {"src/hub.py"}
