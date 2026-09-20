"""A deleted path has no head side, so "no tests cover it" is not a gap.

Without the change status every path is treated as present, and a deletion
reads as an uncovered file -- overstating the coverage debt of a change whose
whole point was to remove the code. These tests pin both readings.
"""

from __future__ import annotations

from repowise.core.analysis.test_impact import analyze_test_impact
from tests.unit.persistence.helpers import insert_repo
from tests.unit.persistence.test_pr_test_impact_semantics import _fixture, _seed


async def _impact(session, repo_id, changed, **kwargs):
    return await analyze_test_impact(session, repo_id, changed, **kwargs)


async def test_a_deleted_path_is_not_counted_as_a_coverage_gap(async_session):
    fixture = _fixture()
    repo = await insert_repo(async_session, name="deleted", head_commit="fixture-head")
    await _seed(async_session, repo.id, fixture)

    gone = "app/removed_module.py"
    impact = await _impact(
        async_session, repo.id, [gone], change_status={gone: "deleted"}
    )

    row = next(r for r in impact["files"] if r["source_file"] == gone)
    assert row["status"] == "deleted"
    assert row["head_present"] is False
    assert row["change_status"] == "deleted"
    assert impact["deleted_files"] == [gone]
    # The two lanes that mean "you are missing tests" must not claim it.
    assert gone not in impact["files_without_measured_tests"]
    assert gone not in impact["unknown_files"]


async def test_without_a_status_the_same_path_reads_as_an_unknown_gap(async_session):
    """The historical behaviour, kept for callers that supply no status."""
    fixture = _fixture()
    repo = await insert_repo(async_session, name="nostatus", head_commit="fixture-head")
    await _seed(async_session, repo.id, fixture)

    gone = "app/removed_module.py"
    impact = await _impact(async_session, repo.id, [gone])

    row = next(r for r in impact["files"] if r["source_file"] == gone)
    assert row["status"] == "unknown"
    assert row["head_present"] is True
    assert row["change_status"] is None
    assert impact["deleted_files"] == []
    assert gone in impact["files_without_measured_tests"]


async def test_a_deleted_path_that_tests_did_cover_still_reports_them(async_session):
    """Deletion is not a reason to hide the tests that exercised the file."""
    fixture = _fixture()
    repo = await insert_repo(async_session, name="covereddel", head_commit="fixture-head")
    await _seed(async_session, repo.id, fixture)

    covered = fixture["coverage"][0]["source_file"]
    impact = await _impact(
        async_session, repo.id, [covered], change_status={covered: "deleted"}
    )

    row = next(r for r in impact["files"] if r["source_file"] == covered)
    assert row["measured_tests"]
    # Measured evidence wins the status: there is something concrete to run.
    assert row["status"] == "measured"
    assert row["head_present"] is False
    assert impact["deleted_files"] == []


async def test_modified_and_added_paths_are_unaffected(async_session):
    fixture = _fixture()
    repo = await insert_repo(async_session, name="mixed", head_commit="fixture-head")
    await _seed(async_session, repo.id, fixture)

    changed = ["app/added.py", "app/modified.py"]
    impact = await _impact(
        async_session,
        repo.id,
        changed,
        change_status={"app/added.py": "added", "app/modified.py": "modified"},
    )

    assert impact["deleted_files"] == []
    assert all(row["head_present"] for row in impact["files"])
    assert sorted(impact["files_without_measured_tests"]) == sorted(changed)


async def test_a_deletion_alongside_a_real_gap_separates_the_two(async_session):
    fixture = _fixture()
    repo = await insert_repo(async_session, name="both", head_commit="fixture-head")
    await _seed(async_session, repo.id, fixture)

    impact = await _impact(
        async_session,
        repo.id,
        ["app/gone.py", "app/kept.py"],
        change_status={"app/gone.py": "deleted", "app/kept.py": "modified"},
    )

    assert impact["deleted_files"] == ["app/gone.py"]
    assert impact["files_without_measured_tests"] == ["app/kept.py"]
