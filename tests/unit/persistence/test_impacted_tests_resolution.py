"""The ``impacted-tests`` resolution path: changed lines -> impacted tests.

Exercises :func:`_resolve_impacted` against a seeded ``test_coverage`` table so
the honest outcomes are pinned and stay distinguishable: a coverage-backed hit,
an inferred candidate when a changed file has no coverage rows (the import graph
first, a filename-pattern guess second), and "unknown" when nothing knows of a
test. The diff parser and CRUD line-intersection are tested separately.
"""

from __future__ import annotations

from repowise.cli.commands.impacted_tests_cmd import _empty_result, _resolve_impacted
from repowise.core.analysis.health.coverage import TestCoverage
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
