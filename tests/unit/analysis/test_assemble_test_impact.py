"""assemble_test_impact over plain records, with no session."""

from __future__ import annotations

from repowise.core.analysis.test_impact import assemble_test_impact

_SUMMARY = {
    "pair_count": 2,
    "test_count": 2,
    "source_file_count": 2,
    "ingested_commit_sha": "head",
    "source_format": "coverage.py",
}


def _assemble(**kwargs):
    return assemble_test_impact(
        ["src/a.py", "src/gone.py", "src/b.py"],
        {"src/a.py": [{"test_id": "tests/test_a.py::t", "test_file": "tests/test_a.py"}]},
        {"src/b.py": {"tests": ["tests/test_b.py"], "via": "imports"}},
        _SUMMARY,
        repository_id="r1",
        indexed_commit="head",
        **kwargs,
    )


def test_deleted_path_is_not_a_coverage_gap() -> None:
    impact = _assemble(change_status={"src/gone.py": "deleted"})
    gone = next(r for r in impact["files"] if r["source_file"] == "src/gone.py")
    assert (gone["status"], gone["head_present"], gone["change_status"]) == (
        "deleted",
        False,
        "deleted",
    )
    assert impact["deleted_files"] == ["src/gone.py"]
    assert impact["files_without_measured_tests"] == ["src/b.py"]
    assert impact["unknown_files"] == []
    assert [r["test_id"] for r in impact["recommendations"]] == [
        "tests/test_a.py::t",
        "tests/test_b.py",
    ]
    assert impact["recommendations"][0]["repository"] == "r1"


def test_without_status_a_missing_path_is_unknown() -> None:
    impact = _assemble()
    assert impact["deleted_files"] == []
    assert impact["unknown_files"] == ["src/gone.py"]


def test_read_failures_mark_each_side_degraded() -> None:
    impact = _assemble(coverage_error="OperationalError", inference_error="TimeoutError")
    assert impact["coverage"]["reason"] == "OperationalError: coverage_query_failed"
    assert impact["inference"]["reason"] == "TimeoutError: test_reachability_failed"
    assert impact["analysis"]["status"] == "degraded"


_INFERRED = {
    "src/b.py": {"tests": ["tests/unit/conftest.py", "tests/test_b.py"], "via": "imports"},
    "src/c.py": {"tests": ["tests/unit/conftest.py"], "via": "call-graph"},
}
_TEST_FILES = {
    "tests/unit/conftest.py",
    "tests/unit/test_x.py",
    "tests/unit/helpers.py",
    "tests/test_b.py",
}


def test_a_conftest_the_walk_reached_stands_for_the_tests_under_it() -> None:
    """pytest collects nothing from a conftest; it runs for the tests below it."""
    impact = assemble_test_impact(
        ["src/b.py", "src/c.py"], {}, _INFERRED, {}, repository_id="r1", repository_test_files=_TEST_FILES
    )
    assert [r["test_id"] for r in impact["recommendations"]] == [
        "tests/unit/test_x.py",
        "tests/test_b.py",
    ]
    c = next(r for r in impact["files"] if r["source_file"] == "src/c.py")
    assert (c["status"], c["inferred_tests"]) == ("inferred", ["tests/unit/test_x.py"])
    assert impact["unknown_files"] == []


def test_without_the_test_files_a_conftest_drops_out() -> None:
    impact = assemble_test_impact(["src/b.py", "src/c.py"], {}, _INFERRED, {}, repository_id="r1")
    assert [r["test_id"] for r in impact["recommendations"]] == ["tests/test_b.py"]
    assert impact["unknown_files"] == ["src/c.py"]


def test_a_root_conftest_expansion_is_ranked_and_capped_per_file() -> None:
    """A root conftest stands for every test; it must not flood the result."""
    from repowise.core.analysis.test_reachability import MAX_TESTS_PER_TARGET

    many = {f"tests/aaa/test_{i:03}.py" for i in range(MAX_TESTS_PER_TARGET + 20)}
    impact = assemble_test_impact(
        ["src/core.py"],
        {},
        {"src/core.py": {"tests": ["conftest.py"], "via": "call-graph"}},
        {},
        repository_id="r1",
        repository_test_files={"conftest.py", "tests/unit/test_core.py", *many},
    )
    row = impact["files"][0]
    assert len(row["inferred_tests"]) == MAX_TESTS_PER_TARGET
    assert row["inferred_tests_total"] == MAX_TESTS_PER_TARGET + 21
    assert "tests/unit/test_core.py" in row["inferred_tests"]  # nearest kept, not first
    assert impact["recommendations_total"] == MAX_TESTS_PER_TARGET
    assert impact["inference"]["candidates_before_dedup"] == MAX_TESTS_PER_TARGET + 21


def test_expand_test_scopes_replaces_only_scope_files() -> None:
    from repowise.core.analysis.test_selection import expand_test_scopes

    files = {"tests/a/test_one.py", "tests/a/helpers.py", "tests/b/test_two.py", "tests/b/__init__.py"}
    assert expand_test_scopes(
        ["tests/test_conftest.py", "tests/a/conftest.py", "tests/a/helpers.py", "tests/a/test_one.py"],
        files,
    ) == ["tests/test_conftest.py", "tests/a/test_one.py", "tests/a/helpers.py"]
    assert expand_test_scopes(["tests/b/__init__.py"], files) == ["tests/b/test_two.py"]
    assert expand_test_scopes(["conftest.py"], files) == [
        "tests/a/test_one.py",
        "tests/b/test_two.py",
    ]


def test_nothing_changed() -> None:
    impact = assemble_test_impact([], {}, {}, {}, repository_id="r1")
    assert impact["files"] == []
    assert impact["coverage"]["reason"] == "no_changed_files"
