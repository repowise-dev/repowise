"""Each function's nearest test functions: the walk, the store, and the plans that quote them."""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from repowise.core.analysis.execution_graph import ExecutionGraphIndex
from repowise.core.analysis.function_test_map import (
    MappedTest,
    decode_tests,
    encode_tests,
    function_test_map,
    runnable_selector,
)
from repowise.core.analysis.health import HealthAnalyzer
from repowise.core.analysis.health.refactoring.models import RefactoringSuggestion
from repowise.core.analysis.health.refactoring.recommendations import (
    ValidationEvidence,
    build_validation_plan,
)
from repowise.core.analysis.test_reachability import ReachedBy

T = "tests/test_core.py"


def _index(calls: dict[str, list[str]]) -> ExecutionGraphIndex:
    declares: dict[str, list[str]] = {}
    for symbol in {*calls, *(c for callees in calls.values() for c in callees)}:
        declares.setdefault(symbol.split("::", 1)[0], []).append(symbol)
    return ExecutionGraphIndex(declares=declares, calls=calls)


def test_only_collected_tests_are_selectors() -> None:
    assert runnable_selector(f"{T}::test_parse") == f"{T}::test_parse"
    assert runnable_selector(f"{T}::TestParse::test_empty") == f"{T}::TestParse::test_empty"
    assert runnable_selector(f"{T}::_helper") is None
    assert runnable_selector(f"{T}::__module__") is None
    # A JS suite's ``it`` callbacks sit on the module: the file runs them.
    assert runnable_selector("src/a.test.ts::__module__") == "src/a.test.ts"
    # Go's own rule: ``TestXxx``, not the ``TestMain`` entry or a ``testHelper``.
    assert runnable_selector("pkg/a_test.go::TestParse") == "pkg/a_test.go::TestParse"
    assert runnable_selector("pkg/a_test.go::TestMain") is None
    assert runnable_selector("pkg/a_test.go::testHelper") is None
    # A case marked by an annotation cannot be read off a name: never guessed.
    assert runnable_selector("src/FooTest.java::FooTest::testParse") is None
    assert runnable_selector("src/lib_test.rs::test_parse") is None


def test_nearest_tests_walk_through_test_helpers_and_stop_at_three_hops() -> None:
    index = _index(
        {
            f"{T}::test_parse": ["core.py::parse"],
            f"{T}::test_via_helper": [f"{T}::_helper"],
            f"{T}::_helper": ["core.py::deep"],
            "core.py::deep": ["core.py::deeper"],
            "core.py::deeper": ["core.py::deepest"],
            "src/a.test.ts::__module__": ["src/a.ts::load"],
            "core.py::orphan": ["core.py::called_by_orphan"],
            "src/Foo.java::Foo::run": ["src/Foo.java::Foo::step"],
        }
    )
    found = function_test_map(index, {T, "src/a.test.ts"})
    assert found["core.py::parse"] == (MappedTest(f"{T}::test_parse", 1),)
    assert found["core.py::deep"] == (MappedTest(f"{T}::test_via_helper", 2),)
    assert found["core.py::deeper"] == (MappedTest(f"{T}::test_via_helper", 3),)
    # Called by resolved edges, and no test within three hops: mapped, untested.
    assert found["core.py::deepest"] == ()
    assert found["core.py::called_by_orphan"] == ()
    # No resolved caller at all (a framework hook, dispatch by name): unknown,
    # never untested.
    assert "core.py::orphan" not in found
    # A language whose tests cannot be seeded from names: unknown, never untested.
    assert "src/Foo.java::Foo::step" not in found
    assert found["src/a.ts::load"] == (MappedTest("src/a.test.ts", 1),)
    # Test-file symbols are walked through, never mapped.
    assert not any(node.startswith(T) for node in found)


def test_each_function_keeps_k_tests_nearest_first_naming_it_first() -> None:
    calls = {f"tests/test_other.py::test_{n}": ["core.py::parse"] for n in "abcdef"}
    calls["tests/test_other.py::test_parse_empty"] = ["core.py::parse"]
    calls["tests/test_z.py::test_far"] = ["core.py::outer"]
    calls["core.py::outer"] = ["core.py::parse"]
    found = function_test_map(_index(calls), {"tests/test_other.py", "tests/test_z.py"}, k=3)
    assert [t.selector for t in found["core.py::parse"]] == [
        "tests/test_other.py::test_parse_empty",
        "tests/test_other.py::test_a",
        "tests/test_other.py::test_b",
    ]
    assert all(t.hops == 1 for t in found["core.py::parse"])
    # A test named for the file ranks before a stranger at the same distance.
    named = function_test_map(
        _index({"tests/test_x.py::test_a": ["core.py::f"], "tests/test_core.py::test_b": ["core.py::f"]}),
        {"tests/test_x.py", "tests/test_core.py"},
        k=1,
    )
    assert named["core.py::f"] == (MappedTest("tests/test_core.py::test_b", 1),)


def test_the_stored_form_round_trips_and_rejects_garbage() -> None:
    tests = (MappedTest(f"{T}::test_a", 1), MappedTest("src/a.test.ts", 2))
    assert decode_tests(encode_tests(tests)) == tests
    assert decode_tests("[]") == ()
    assert decode_tests(None) is None
    assert decode_tests("{") is None


def test_the_analyzer_maps_tests_over_the_whole_graph() -> None:
    graph = nx.DiGraph()
    graph.add_node(T, node_type="file", is_test=True)
    graph.add_node("core.py", node_type="file", is_test=False)
    for symbol, path in ((f"{T}::test_parse", T), ("core.py::parse", "core.py")):
        graph.add_node(symbol, node_type="symbol", name=symbol.rsplit("::", 1)[-1], file_path=path)
        graph.add_edge(path, symbol, edge_type="defines")
    graph.add_edge(f"{T}::test_parse", "core.py::parse", edge_type="calls")
    assert HealthAnalyzer(graph).test_index.function_tests() == {
        "core.py::parse": (MappedTest(f"{T}::test_parse", 1),)
    }
    assert HealthAnalyzer(None).test_index.function_tests() is None


@pytest.fixture
async def store(tmp_path: Path):
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )

    engine = create_engine(f"sqlite+aiosqlite:///{(tmp_path / 'wiki.db').as_posix()}")
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="repo", local_path=str(tmp_path))
            await session.commit()
            yield session, repo.id
    finally:
        await engine.dispose()


async def test_tests_are_stamped_on_every_row_and_read_back_nearest_first(store) -> None:
    from sqlalchemy import event

    from repowise.core.persistence.crud import tests_for_functions, write_function_facts

    session, repo = store
    near = (MappedTest(f"{T}::test_a", 1), MappedTest(f"{T}::test_b", 2))
    rows = [{"symbol_id": "a.py::f"}, {"symbol_id": "a.py::g"}, {"symbol_id": "b.py::h"}]
    await write_function_facts(session, repo, rows)
    assert await tests_for_functions(session, repo, ["a.py::f"]) == {}
    await write_function_facts(session, repo, rows, tests={"a.py::f": near, "a.py::g": ()})
    # b.py::h is not in the map: stored NULL, never mapped, so absent here.
    assert await tests_for_functions(session, repo, ["a.py::f", "a.py::g", "b.py::h"]) == {
        "a.py::f": near,
        "a.py::g": (),
    }
    assert await tests_for_functions(session, repo, ["a.py::f"], k=1) == {"a.py::f": near[:1]}

    # An update walking only b.py: a test moved for a.py::g, so only its row is written.
    updates: list[int] = []
    engine = session.bind.sync_engine

    def count(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE function_facts"):
            updates.append(len(parameters) if executemany else 1)

    event.listen(engine, "before_cursor_execute", count)
    try:
        await write_function_facts(
            session,
            repo,
            [{"symbol_id": "b.py::h"}],
            file_paths=["b.py"],
            tests={"a.py::f": near, "a.py::g": near[1:]},
        )
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert sum(updates) == 1
    assert (await tests_for_functions(session, repo, ["a.py::g"]))["a.py::g"] == near[1:]

    from repowise.core.persistence.crud.analysis.function_facts import tests_in_files
    from repowise.core.persistence.models import GraphNode

    for symbol in ("a.py::f", "a.py::g", "b.py::h"):
        path = symbol.split("::", 1)[0]
        session.add(
            GraphNode(repository_id=repo, node_id=symbol, node_type="symbol", file_path=path)
        )
    await session.flush()
    assert await tests_in_files(session, repo, ["a.py"]) == {"a.py::f": near, "a.py::g": near[1:]}


def _extract(file_path: str = "core.py") -> RefactoringSuggestion:
    return RefactoringSuggestion(
        refactoring_type="extract_method",
        file_path=file_path,
        target_symbol="parse",
        line_start=10,
        line_end=20,
        plan={},
        evidence={},
        impact_delta=2.0,
        effort_bucket="M",
        blast_radius={},
        confidence="high",
        source_biomarker="long_function",
    )


_FILE_REACH = {
    "core.py": ReachedBy(
        ["tests/test_core.py", "tests/test_misc.py"],
        "call-graph",
        2,
        ("tests/test_core.py", "tests/test_misc.py"),
    )
}


def _evidence(tests: tuple[MappedTest, ...]) -> ValidationEvidence:
    return ValidationEvidence(
        symbols={"core.py": [("core.py::parse", 5, 30)]},
        function_tests={"core.py::parse": tests},
    )


def test_a_plan_quotes_the_test_functions_that_call_its_function() -> None:
    near = (
        MappedTest(f"{T}::test_parse", 1),
        MappedTest("tests/test_io.py::test_read", 1),
        MappedTest(f"{T}::test_load", 2),
    )
    plan = build_validation_plan(_extract(), {}, _FILE_REACH, evidence=_evidence(near))
    # A test that only reaches a caller is not quoted: it may never take the call.
    assert plan.tests == [f"{T}::test_parse", "tests/test_io.py::test_read"]
    # The quoted tests' files, whole: at least the tests quoted, in a short command.
    assert plan.commands == [f"pytest {T} tests/test_io.py"]
    assert plan.reasons == {
        f"{T}::test_parse": "calls parse",
        "tests/test_io.py::test_read": "calls parse",
    }
    assert (plan.basis, plan.via, plan.total, plan.prerequisite) == ("inferred", "call-graph", 2, None)
    # The file's own reach stays on the target row.
    assert plan.targets[0].tests == ["tests/test_core.py", "tests/test_misc.py"]


def test_a_function_reached_only_through_callers_keeps_the_file_answer() -> None:
    near = (MappedTest(f"{T}::test_load", 2),)
    plan = build_validation_plan(_extract(), {}, _FILE_REACH, evidence=_evidence(near))
    assert plan.tests == ["tests/test_core.py", "tests/test_misc.py"]
    assert plan.prerequisite is None


def test_a_function_no_test_reaches_asks_for_a_characterization_test_in_a_tested_file() -> None:
    plan = build_validation_plan(_extract(), {}, _FILE_REACH, evidence=_evidence(()))
    assert plan.tests == ["tests/test_core.py", "tests/test_misc.py"]
    assert plan.prerequisite == (
        "No test reaches `parse` (2 reach its file); add a characterization test first."
    )


def test_an_unmapped_function_keeps_the_file_level_answer() -> None:
    plan = build_validation_plan(
        _extract(), {}, _FILE_REACH, evidence=ValidationEvidence(symbols=_evidence(()).symbols)
    )
    assert plan.tests == ["tests/test_core.py", "tests/test_misc.py"]
    assert plan.prerequisite is None


def test_measured_coverage_outranks_the_map() -> None:
    measured = {"core.py": [{"test_id": "tests/test_core.py::test_cov", "covered_lines": [12]}]}
    near = (MappedTest(f"{T}::test_parse", 1),)
    plan = build_validation_plan(_extract(), measured, {}, evidence=_evidence(near))
    assert plan.basis == "measured"
    assert plan.tests == ["tests/test_core.py::test_cov"]


def test_a_capped_list_quotes_the_nearest_and_counts_the_rest() -> None:
    near = (MappedTest(f"{T}::test_parse", 1), MappedTest("tests/test_io.py::test_load", 1))
    plan = build_validation_plan(_extract(), {}, _FILE_REACH, test_limit=1, evidence=_evidence(near))
    assert plan.tests == [f"{T}::test_parse"]
    assert plan.truncated is True
    assert plan.commands == [f"pytest {T}"]


async def test_validation_inputs_carry_spans_and_tests_for_files_that_are_no_hub(store) -> None:
    """The planless-finding path reads only these inputs, so a non-hub file needs
    its spans and nearest tests here, not only in the plan path's second read."""
    from repowise.core.persistence.crud import write_function_facts
    from repowise.core.persistence.crud.analysis.refactoring_recommendations import (
        validation_inputs,
    )
    from repowise.core.persistence.models import GraphNode

    session, repo = store
    session.add(
        GraphNode(
            repository_id=repo,
            node_id="core.py::parse",
            node_type="symbol",
            file_path="core.py",
            start_line=5,
            end_line=30,
        )
    )
    near = (MappedTest(f"{T}::test_parse", 1),)
    await write_function_facts(
        session, repo, [{"symbol_id": "core.py::parse"}], tests={"core.py::parse": near}
    )
    inputs = await validation_inputs(session, repo, [_extract()], ["core.py"], in_degree={})
    assert inputs.evidence.symbols["core.py"] == [("core.py::parse", 5, 30)]
    assert inputs.evidence.function_tests == {"core.py::parse": near}
    plan = build_validation_plan(
        _extract(), inputs.measured, inputs.inferred, evidence=inputs.evidence, order_tests=False
    )
    assert plan.tests == [f"{T}::test_parse"]
