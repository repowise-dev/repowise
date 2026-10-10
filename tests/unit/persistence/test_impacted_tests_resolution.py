"""The ``impacted-tests`` resolution path: changed lines -> impacted tests.

Exercises :func:`resolve_impacted` against a seeded ``test_coverage`` table so
the honest outcomes are pinned and stay distinguishable: a coverage-backed hit,
an inferred candidate when a changed file has no coverage rows (the import graph
first, a filename-pattern guess second), and "unknown" when nothing knows of a
test. The diff parser and CRUD line-intersection are tested separately.
"""

from __future__ import annotations

from repowise.core.analysis.health.coverage import TestCoverage
from repowise.core.analysis.test_collection import empty_result as _empty_result
from repowise.core.analysis.test_collection import resolve_impacted as _resolve_impacted
from repowise.core.persistence.crud import save_test_coverage
from tests.unit.persistence.helpers import insert_repo

# Repo tree the filename-pattern fallback resolves guesses against.
_REPO_KEYS = {
    "src/foo.py",
    "src/bar.py",
    "src/lonely.py",
    "tests/test_bar.py",
}


def _rec(test_id: str, source_file: str, lines: list[int], test_file: str):
    return TestCoverage(
        test_id=test_id,
        file_path=source_file,
        covered_lines=lines,
        source_format="coverage.py",
        test_file=test_file,
    )


async def _seed(async_session):
    repo = await insert_repo(async_session)
    await save_test_coverage(
        async_session,
        repo.id,
        [
            _rec("tests/test_foo.py::test_a|run", "src/foo.py", [1, 2, 3], "tests/test_foo.py"),
            _rec("tests/test_foo.py::test_b|run", "src/foo.py", [8, 9], "tests/test_foo.py"),
        ],
        source_format="coverage.py",
    )
    await async_session.commit()
    return repo


async def test_covered_change_returns_exact_tests(async_session) -> None:
    repo = await _seed(async_session)
    out = _empty_result(1)
    await _resolve_impacted(async_session, repo.id, {"src/foo.py": {2}}, _REPO_KEYS, out)

    assert set(out["covered"]) == {"tests/test_foo.py::test_a|run"}
    assert out["covered"]["tests/test_foo.py::test_a|run"]["source_files"] == ["src/foo.py"]
    assert out["inferred"] == []
    assert out["unknown"] == []


async def test_change_missing_covered_lines_falls_to_guess_or_unknown(async_session) -> None:
    repo = await _seed(async_session)
    out = _empty_result(3)
    # foo.py line 99 has no covering test; bar.py has no coverage at all but a
    # paired test; lonely.py has neither coverage nor a paired test.
    changed = {"src/foo.py": {99}, "src/bar.py": {5}, "src/lonely.py": {1}}
    await _resolve_impacted(async_session, repo.id, changed, _REPO_KEYS, out)

    # foo.py: had rows for the file but none intersect line 99, and its pair
    # (test_foo.py) is NOT in _REPO_KEYS, so it lands in unknown.
    assert "src/foo.py" in out["unknown"]
    assert "src/lonely.py" in out["unknown"]
    # No graph rows are seeded here, so the filename pattern is what answers.
    assert out["inferred"] == [
        {"source_file": "src/bar.py", "test_file": "tests/test_bar.py", "via": "filename-pattern"}
    ]
    assert out["covered"] == {}


async def test_one_test_covering_multiple_changed_files_dedupes(async_session) -> None:
    repo = await insert_repo(async_session)
    await save_test_coverage(
        async_session,
        repo.id,
        [
            _rec("t::shared|run", "src/foo.py", [1], "tests/t.py"),
            _rec("t::shared|run", "src/bar.py", [2], "tests/t.py"),
        ],
        source_format="coverage.py",
    )
    await async_session.commit()

    out = _empty_result(2)
    await _resolve_impacted(
        async_session, repo.id, {"src/foo.py": {1}, "src/bar.py": {2}}, _REPO_KEYS, out
    )
    assert set(out["covered"]) == {"t::shared|run"}
    assert sorted(out["covered"]["t::shared|run"]["source_files"]) == ["src/bar.py", "src/foo.py"]


async def test_import_graph_answers_before_the_filename_pattern(async_session) -> None:
    """A recorded edge beats a name-shaped guess, and says so.

    ``src/bar.py`` has both: a graph edge from a behaviour-named test and a
    conventionally named ``tests/test_bar.py``. The graph is the one with
    evidence behind it, so it is the one reported.
    """
    from repowise.core.persistence.models import GraphEdge, GraphNode

    repo = await insert_repo(async_session)
    for path, is_test in (
        ("tests/test_round_trips.py", True),
        ("tests/test_bar.py", True),
        ("src/bar.py", False),
    ):
        async_session.add(
            GraphNode(repository_id=repo.id, node_id=path, node_type="file", is_test=is_test)
        )
    async_session.add(
        GraphEdge(
            repository_id=repo.id,
            source_node_id="tests/test_round_trips.py",
            target_node_id="src/bar.py",
            edge_type="imports",
        )
    )
    await async_session.commit()

    out = _empty_result(1)
    await _resolve_impacted(async_session, repo.id, {"src/bar.py": {5}}, _REPO_KEYS, out)

    assert out["inferred"] == [
        {
            "source_file": "src/bar.py",
            "test_file": "tests/test_round_trips.py",
            "via": "import-graph",
        }
    ]
    assert out["unknown"] == []


async def test_a_changed_test_file_is_its_own_candidate(async_session) -> None:
    """"That test has no test" is true and useless; run the test you changed."""
    from repowise.core.persistence.models import GraphNode

    repo = await insert_repo(async_session)
    async_session.add(
        GraphNode(
            repository_id=repo.id,
            node_id="tests/test_bar.py",
            node_type="file",
            is_test=True,
        )
    )
    await async_session.commit()

    out = _empty_result(1)
    await _resolve_impacted(async_session, repo.id, {"tests/test_bar.py": {1}}, _REPO_KEYS, out)

    assert out["inferred"] == [
        {
            "source_file": "tests/test_bar.py",
            "test_file": "tests/test_bar.py",
            "via": "changed-test",
        }
    ]
    assert out["unknown"] == []


async def _graph(session, repo_id, tests: set[str], files: set[str], imports: list[tuple]):
    """File nodes (tests flagged) and ``imports`` edges ``(importer, imported)``."""
    from repowise.core.persistence.models import GraphEdge, GraphNode

    for path in sorted(tests | files):
        session.add(
            GraphNode(repository_id=repo_id, node_id=path, node_type="file", is_test=path in tests)
        )
    for source, target in imports:
        session.add(
            GraphEdge(
                repository_id=repo_id,
                source_node_id=source,
                target_node_id=target,
                edge_type="imports",
            )
        )
    await session.commit()


def _pairs(out: dict, source: str) -> dict[str, str]:
    return {g["test_file"]: g["via"] for g in out["inferred"] if g["source_file"] == source}


async def test_the_import_graph_reaches_tests_through_modules_and_other_tests(
    async_session,
) -> None:
    """A test two imports away, and one importing a test that reaches it, still run it."""
    repo = await insert_repo(async_session)
    await _graph(
        async_session,
        repo.id,
        tests={"tests/test_api.py", "tests/base.py", "tests/test_child.py"},
        files={"src/api.py", "src/core.py"},
        imports=[
            ("src/api.py", "src/core.py"),
            ("tests/test_api.py", "src/api.py"),
            ("tests/base.py", "src/core.py"),
            ("tests/test_child.py", "tests/base.py"),
        ],
    )

    out = _empty_result(1)
    await _resolve_impacted(async_session, repo.id, {"src/core.py": {1}}, set(), out)

    assert _pairs(out, "src/core.py") == {
        "tests/base.py": "import-graph",
        "tests/test_api.py": "import-graph",
        "tests/test_child.py": "import-graph",
    }
    assert out["unknown"] == []


async def test_graph_candidates_are_not_capped(async_session) -> None:
    from repowise.core.analysis.test_reachability import MAX_TESTS_PER_TARGET

    repo = await insert_repo(async_session)
    tests = {f"tests/test_{i:03}.py" for i in range(MAX_TESTS_PER_TARGET + 5)}
    await _graph(
        async_session, repo.id, tests, {"src/hub.py"}, [(t, "src/hub.py") for t in tests]
    )

    out = _empty_result(1)
    await _resolve_impacted(async_session, repo.id, {"src/hub.py": {1}}, set(), out)
    assert set(_pairs(out, "src/hub.py")) == tests


async def test_a_file_level_lookup_also_asks_the_graph(async_session) -> None:
    """Matched by file (a deleted file, a stale map), coverage alone is not enough."""
    repo = await _seed(async_session)
    await _graph(
        async_session,
        repo.id,
        tests={"tests/test_other.py"},
        files={"src/foo.py"},
        imports=[("tests/test_other.py", "src/foo.py")],
    )

    out = _empty_result(1)
    await _resolve_impacted(async_session, repo.id, {"src/foo.py": None}, _REPO_KEYS, out)

    assert set(out["covered"]) == {
        "tests/test_foo.py::test_a|run",
        "tests/test_foo.py::test_b|run",
    }
    assert _pairs(out, "src/foo.py") == {"tests/test_other.py": "import-graph"}
    assert out["unknown"] == []


async def test_coverage_adds_to_the_graph_and_never_replaces_it(async_session) -> None:
    """A test the coverage run missed still runs when the graph shows it reaching the file."""
    repo = await _seed(async_session)
    await _graph(
        async_session,
        repo.id,
        tests={"tests/test_other.py"},
        files={"src/foo.py"},
        imports=[("tests/test_other.py", "src/foo.py")],
    )

    out = _empty_result(1)
    await _resolve_impacted(async_session, repo.id, {"src/foo.py": {2}}, _REPO_KEYS, out)

    assert set(out["covered"]) == {"tests/test_foo.py::test_a|run"}
    assert _pairs(out, "src/foo.py") == {"tests/test_other.py": "import-graph"}


async def test_a_graph_read_failure_is_reported_not_swallowed(async_session, monkeypatch) -> None:
    from repowise.core.analysis import test_collection

    async def _broken(*_a, **_k):
        raise RuntimeError("edge table locked")

    monkeypatch.setattr(test_collection, "_graph_candidates", _broken)
    repo = await insert_repo(async_session)
    out = _empty_result(1)
    await _resolve_impacted(async_session, repo.id, {"src/a.py": {1}}, set(), out)
    assert out["graph_error"] == "RuntimeError: edge table locked"


async def test_data_files_are_not_routes_and_helper_importers_are_reported(
    async_session,
) -> None:
    """A JSON file flagged as test material is data; a helper's importers are listed."""
    repo = await insert_repo(async_session)
    await _graph(
        async_session,
        repo.id,
        tests={"tests/golden/out.json", "tests/helpers.py", "tests/test_a.py"},
        files={"src/a.py"},
        imports=[
            ("tests/golden/out.json", "src/a.py"),
            ("tests/helpers.py", "src/a.py"),
            ("tests/test_a.py", "tests/helpers.py"),
        ],
    )

    out = _empty_result(1)
    await _resolve_impacted(async_session, repo.id, {"src/a.py": {1}}, set(), out)

    assert _pairs(out, "src/a.py") == {
        "tests/helpers.py": "import-graph",
        "tests/test_a.py": "import-graph",
    }
    assert out["helper_importers"]["tests/helpers.py"] == ["tests/test_a.py"]


_CONFTEST = '''
import pytest
from app import store


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(store, "_cache", None)


@pytest.fixture
def db():
    return store.open()
'''


async def _conftest_graph(session, repo_id, *, stamped: bool) -> None:
    """A conftest importing ``store``, one test asking for its ``db`` fixture, two not."""
    from repowise.core.persistence.models import GraphEdge, GraphNode

    tests = ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]
    await _graph(
        session,
        repo_id,
        tests={*tests, "tests/conftest.py"},
        files={"src/app/store.py", "src/app/util.py"},
        imports=[("src/app/store.py", "src/app/util.py"), ("tests/conftest.py", "src/app/store.py")],
    )
    for sym in ("tests/conftest.py::db", "tests/conftest.py::_isolate", "tests/test_a.py::test_x"):
        session.add(GraphNode(repository_id=repo_id, node_id=sym, node_type="symbol"))
    for test in tests:
        session.add(
            GraphEdge(
                repository_id=repo_id,
                source_node_id=test,
                target_node_id="tests/conftest.py",
                edge_type="framework",
                hint_source="pytest_conftest",
            )
        )
    session.add(
        GraphEdge(
            repository_id=repo_id,
            source_node_id="tests/test_a.py::test_x",
            target_node_id="tests/conftest.py::db",
            edge_type="framework_binds",
            hint_source="pytest_fixture" if stamped else None,
        )
    )
    await session.commit()


async def test_a_conftest_reached_through_its_imports_runs_only_the_tests_it_can_break(
    async_session,
) -> None:
    """Only ``db`` runs the changed code; the autouse fixture just patches it."""
    repo = await insert_repo(async_session)
    await _conftest_graph(async_session, repo.id, stamped=True)

    out = _empty_result(1)
    await _resolve_impacted(
        async_session,
        repo.id,
        {"src/app/util.py": None},
        set(),
        out,
        pytest_texts={"tests/conftest.py": _CONFTEST},
    )

    assert _pairs(out, "src/app/util.py") == {"tests/test_a.py": "conftest-fixture"}
    assert any("only through its imports" in n for n in out["conftest_notes"])


async def test_an_index_without_stamped_fixture_requests_keeps_every_test(async_session) -> None:
    repo = await insert_repo(async_session)
    await _conftest_graph(async_session, repo.id, stamped=False)

    out = _empty_result(1)
    await _resolve_impacted(
        async_session,
        repo.id,
        {"src/app/util.py": None},
        set(),
        out,
        pytest_texts={"tests/conftest.py": _CONFTEST},
    )

    assert "tests/conftest.py" in _pairs(out, "src/app/util.py")
    assert any("before fixture requests were recorded" in n for n in out["conftest_notes"])


async def test_a_conftest_only_patching_the_route_runs_one_import_check(async_session) -> None:
    repo = await insert_repo(async_session)
    await _conftest_graph(async_session, repo.id, stamped=True)
    source = _CONFTEST.split("@pytest.fixture\ndef db")[0]

    out = _empty_result(1)
    await _resolve_impacted(
        async_session,
        repo.id,
        {"src/app/util.py": None},
        set(),
        out,
        pytest_texts={"tests/conftest.py": source},
    )

    assert _pairs(out, "src/app/util.py") == {"tests/test_a.py": "conftest-import-check"}


async def _add_edge(session, repo_id, source, target, edge_type, hint=None) -> None:
    from repowise.core.persistence.models import GraphEdge

    session.add(
        GraphEdge(
            repository_id=repo_id,
            source_node_id=source,
            target_node_id=target,
            edge_type=edge_type,
            hint_source=hint,
        )
    )
    await session.commit()


async def _narrowed(session, repo_id) -> dict[str, str]:
    out = _empty_result(1)
    await _resolve_impacted(
        session,
        repo_id,
        {"src/app/util.py": None},
        set(),
        out,
        pytest_texts={"tests/conftest.py": _CONFTEST},
    )
    return out


async def test_a_request_the_index_cannot_record_keeps_every_test(async_session) -> None:
    from sqlalchemy import update

    from repowise.core.persistence.models import GraphEdge

    repo = await insert_repo(async_session)
    await _conftest_graph(async_session, repo.id, stamped=True)
    await async_session.execute(
        update(GraphEdge)
        .where(GraphEdge.source_node_id == "tests/test_b.py")
        .values(hint_source="pytest_conftest_unrecorded")
    )
    await async_session.commit()

    out = await _narrowed(async_session, repo.id)

    assert "tests/conftest.py" in _pairs(out, "src/app/util.py")
    assert any("tests/test_b.py asks for fixtures" in n for n in out["conftest_notes"])


async def test_an_unresolved_conftest_import_keeps_every_test(async_session) -> None:
    repo = await insert_repo(async_session)
    await _conftest_graph(async_session, repo.id, stamped=True)
    await _add_edge(async_session, repo.id, "tests/test_c.py", "external:conftest", "imports")

    out = await _narrowed(async_session, repo.id)

    assert "tests/conftest.py" in _pairs(out, "src/app/util.py")
    assert any("could not resolve" in n for n in out["conftest_notes"])


async def test_a_resolved_conftest_import_keeps_its_importer(async_session) -> None:
    """``from conftest import helper`` is an import: that test runs whatever it reaches."""
    from sqlalchemy import update

    from repowise.core.persistence.models import GraphEdge

    repo = await insert_repo(async_session)
    await _conftest_graph(async_session, repo.id, stamped=True)
    await async_session.execute(
        update(GraphEdge)
        .where(GraphEdge.source_node_id == "tests/test_c.py", GraphEdge.edge_type == "framework")
        .values(edge_type="imports", hint_source=None)
    )
    await async_session.commit()

    out = await _narrowed(async_session, repo.id)

    assert _pairs(out, "src/app/util.py") == {
        "tests/test_a.py": "conftest-fixture",
        "tests/test_c.py": "conftest-fixture",
    }


async def test_a_reaching_fixture_nobody_requests_selects_only_the_import_check(
    async_session,
) -> None:
    from sqlalchemy import delete

    from repowise.core.persistence.models import GraphEdge

    repo = await insert_repo(async_session)
    await _conftest_graph(async_session, repo.id, stamped=True)
    await async_session.execute(
        delete(GraphEdge).where(GraphEdge.target_node_id == "tests/conftest.py::db")
    )
    # Another fixture request elsewhere shows the index records them.
    await _add_edge(
        async_session, repo.id, "tests/test_b.py::test_x", "tests/other.py::f",
        "framework_binds", "pytest_fixture",
    )

    out = await _narrowed(async_session, repo.id)

    assert _pairs(out, "src/app/util.py") == {"tests/test_a.py": "conftest-import-check"}
