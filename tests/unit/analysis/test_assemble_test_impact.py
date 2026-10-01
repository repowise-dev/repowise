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


def test_nothing_changed() -> None:
    impact = assemble_test_impact([], {}, {}, {}, repository_id="r1")
    assert impact["files"] == []
    assert impact["coverage"]["reason"] == "no_changed_files"
