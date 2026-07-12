"""CRUD for the per-test coverage map (``test_coverage`` table).

Exercises both query directions (reverse index with line intersection,
forward lookup), the point-in-time overwrite, and dedup/cap on save.
"""

from __future__ import annotations

from repowise.core.analysis.health.coverage import TestCoverage
from repowise.core.persistence.crud import (
    files_covered_by,
    get_test_coverage_summary,
    save_test_coverage,
)
from repowise.core.persistence.crud import (
    # Aliased: a bare ``tests_covering`` module global would be collected by
    # pytest as a test function (``test`` prefix).
    tests_covering as find_covering_tests,
)
from tests.unit.persistence.helpers import insert_repo


def _rec(test_id: str, source_file: str, lines: list[int], test_file: str | None = None):
    return TestCoverage(
        test_id=test_id,
        file_path=source_file,
        covered_lines=lines,
        source_format="coverage.py",
        test_file=test_file,
    )


async def test_save_and_query_both_directions(async_session) -> None:
    repo = await insert_repo(async_session)
    records = [
        _rec("tests/test_a.py::test_one|run", "src/foo.py", [1, 2, 3], "tests/test_a.py"),
        _rec("tests/test_a.py::test_one|run", "src/bar.py", [10], "tests/test_a.py"),
        _rec("tests/test_b.py::test_two|run", "src/foo.py", [3, 4, 5], "tests/test_b.py"),
    ]
    written = await save_test_coverage(async_session, repo.id, records, source_format="coverage.py")
    await async_session.commit()
    assert written == 3

    # Reverse: which tests cover src/foo.py at all.
    covering = await find_covering_tests(async_session, repo.id, "src/foo.py")
    ids = {c["test_id"] for c in covering}
    assert ids == {"tests/test_a.py::test_one|run", "tests/test_b.py::test_two|run"}

    # Forward: what does test_one cover.
    covered = await files_covered_by(async_session, repo.id, "tests/test_a.py::test_one|run")
    by_file = {c["source_file"]: c["covered_lines"] for c in covered}
    assert by_file == {"src/foo.py": [1, 2, 3], "src/bar.py": [10]}


async def test_reverse_index_intersects_changed_lines(async_session) -> None:
    repo = await insert_repo(async_session)
    await save_test_coverage(
        async_session,
        repo.id,
        [
            _rec("t_one", "src/foo.py", [1, 2, 3]),
            _rec("t_two", "src/foo.py", [8, 9]),
        ],
        source_format="coverage.py",
    )
    await async_session.commit()

    # Only tests whose covered lines intersect the changed set come back,
    # narrowed to the intersecting lines - the impacted-tests query.
    hits = await find_covering_tests(async_session, repo.id, "src/foo.py", lines={2, 3})
    assert len(hits) == 1
    assert hits[0]["test_id"] == "t_one"
    assert hits[0]["covered_lines"] == [2, 3]

    # A change to line 9 hits only the other test.
    hits = await find_covering_tests(async_session, repo.id, "src/foo.py", lines={9})
    assert [h["test_id"] for h in hits] == ["t_two"]

    # A change touching no covered line returns nothing.
    assert await find_covering_tests(async_session, repo.id, "src/foo.py", lines={100}) == []


async def test_save_overwrites_previous_run(async_session) -> None:
    repo = await insert_repo(async_session)
    await save_test_coverage(
        async_session, repo.id, [_rec("old", "src/foo.py", [1])], source_format="coverage.py"
    )
    await async_session.commit()
    await save_test_coverage(
        async_session, repo.id, [_rec("new", "src/foo.py", [2])], source_format="coverage.py"
    )
    await async_session.commit()

    covering = await find_covering_tests(async_session, repo.id, "src/foo.py")
    assert [c["test_id"] for c in covering] == ["new"]


async def test_save_dedupes_repeated_pairs(async_session) -> None:
    repo = await insert_repo(async_session)
    # Same (test_id, source_file) twice - the unique key. First wins; the
    # second is dropped rather than tripping the constraint.
    written = await save_test_coverage(
        async_session,
        repo.id,
        [
            _rec("t", "src/foo.py", [1, 2]),
            _rec("t", "src/foo.py", [3, 4]),
        ],
        source_format="coverage.py",
    )
    await async_session.commit()
    assert written == 1
    covered = await files_covered_by(async_session, repo.id, "t")
    assert covered[0]["covered_lines"] == [1, 2]


async def test_save_caps_rows(async_session) -> None:
    repo = await insert_repo(async_session)
    records = [_rec(f"t{i}", "src/foo.py", [i]) for i in range(10)]
    written = await save_test_coverage(
        async_session, repo.id, records, source_format="coverage.py", max_rows=4
    )
    await async_session.commit()
    assert written == 4
    covering = await find_covering_tests(async_session, repo.id, "src/foo.py")
    assert len(covering) == 4


async def test_summary_counts(async_session) -> None:
    repo = await insert_repo(async_session)
    empty = await get_test_coverage_summary(async_session, repo.id)
    assert empty["pair_count"] == 0

    await save_test_coverage(
        async_session,
        repo.id,
        [
            _rec("t_one", "src/foo.py", [1]),
            _rec("t_one", "src/bar.py", [2]),
            _rec("t_two", "src/foo.py", [3]),
        ],
        source_format="coverage.py",
    )
    await async_session.commit()

    summary = await get_test_coverage_summary(async_session, repo.id)
    assert summary["pair_count"] == 3
    assert summary["test_count"] == 2
    assert summary["source_file_count"] == 2
    assert summary["source_format"] == "coverage.py"
