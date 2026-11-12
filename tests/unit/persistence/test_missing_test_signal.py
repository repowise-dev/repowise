"""The coverage-backed missing-test detector: changed lines -> two sub-signals.

Exercises :func:`detect_missing_tests` against a seeded ``test_coverage`` table
so the four honest outcomes are pinned:
  * UNTESTED CHANGE - file in the map, changed lines covered by no test;
  * STALE TEST CANDIDATE - covered lines, covering test file not in the diff;
  * NO DATA - file has zero coverage rows (unknown, never "untested");
  * COVERED - covered lines and the covering test file IS in the diff.

Plus the honesty guards: an empty map says nothing, and pathless covering rows
(dynamic-context ids with no test file) never fire the stale signal.
"""

from __future__ import annotations

from repowise.core.analysis.health.coverage import TestCoverage
from repowise.core.analysis.missing_test_signal import detect_missing_tests
from repowise.core.persistence.crud import save_test_coverage
from tests.unit.persistence.helpers import insert_repo


def _rec(test_id: str, source_file: str, lines: list[int], test_file: str | None):
    return TestCoverage(
        test_id=test_id,
        file_path=source_file,
        covered_lines=lines,
        source_format="coverage.py",
        test_file=test_file,
    )


async def _seed(async_session, records):
    repo = await insert_repo(async_session)
    await save_test_coverage(async_session, repo.id, records, source_format="coverage.py")
    await async_session.commit()
    return repo


async def test_untested_change_fires_when_file_covered_but_lines_are_not(async_session):
    # foo.py is exercised (lines 1-3, 8-9), but the change touches line 99.
    repo = await _seed(
        async_session,
        [
            _rec("tests/test_foo.py::test_a", "src/foo.py", [1, 2, 3], "tests/test_foo.py"),
            _rec("tests/test_foo.py::test_b", "src/foo.py", [8, 9], "tests/test_foo.py"),
        ],
    )
    report = await detect_missing_tests(async_session, repo.id, {"src/foo.py": {99}})

    assert len(report.untested_changes) == 1
    uc = report.untested_changes[0]
    assert uc.source_file == "src/foo.py"
    assert uc.uncovered_lines == [99]
    assert uc.changed_line_count == 1
    assert report.stale_test_candidates == []
    assert report.no_data == []
    assert report.covered == []


async def test_stale_test_candidate_fires_when_covering_test_not_in_diff(async_session):
    repo = await _seed(
        async_session,
        [_rec("tests/test_foo.py::test_a", "src/foo.py", [1, 2, 3], "tests/test_foo.py")],
    )
    # Change touches covered line 2, but the diff does NOT include the test file.
    report = await detect_missing_tests(async_session, repo.id, {"src/foo.py": {2}})

    assert len(report.stale_test_candidates) == 1
    stc = report.stale_test_candidates[0]
    assert stc.source_file == "src/foo.py"
    assert stc.covering_test_files == ["tests/test_foo.py"]
    assert report.untested_changes == []
    assert report.covered == []


async def test_covered_when_test_file_is_in_the_diff(async_session):
    repo = await _seed(
        async_session,
        [_rec("tests/test_foo.py::test_a", "src/foo.py", [1, 2, 3], "tests/test_foo.py")],
    )
    # Both the source AND its covering test file are in the diff -> no signal.
    report = await detect_missing_tests(
        async_session, repo.id, {"src/foo.py": {2}, "tests/test_foo.py": {10}}
    )

    assert report.covered == ["src/foo.py"]
    assert report.untested_changes == []
    assert report.stale_test_candidates == []


async def test_no_data_file_is_never_untested(async_session):
    # The map has data for foo.py, but the change touches bar.py (zero rows).
    repo = await _seed(
        async_session,
        [_rec("tests/test_foo.py::test_a", "src/foo.py", [1, 2, 3], "tests/test_foo.py")],
    )
    report = await detect_missing_tests(async_session, repo.id, {"src/bar.py": {5}})

    assert report.no_data == ["src/bar.py"]
    assert report.untested_changes == []
    assert report.stale_test_candidates == []


async def test_empty_map_says_nothing(async_session):
    repo = await insert_repo(async_session)
    report = await detect_missing_tests(async_session, repo.id, {"src/foo.py": {1}})

    assert report.map_empty is True
    assert not report.has_signal()
    assert report.no_data == []


async def test_pathless_covering_rows_do_not_fire_stale(async_session):
    # coverage.py dynamic-context ids carry no test file path: covered, but we
    # cannot tell whether the guarding test was touched -> never "stale".
    repo = await _seed(
        async_session,
        [_rec("foo.test_add", "src/foo.py", [1, 2, 3], None)],
    )
    report = await detect_missing_tests(async_session, repo.id, {"src/foo.py": {2}})

    assert report.covered == ["src/foo.py"]
    assert report.stale_test_candidates == []
    assert report.untested_changes == []


async def test_uncovered_lines_are_narrowed_to_the_change(async_session):
    # Change touches lines 2 (covered) and 99 (not) - since at least one line is
    # covered the file is NOT untested; it is a stale-test candidate, and the
    # covered set is what protects it.
    repo = await _seed(
        async_session,
        [_rec("tests/test_foo.py::test_a", "src/foo.py", [1, 2, 3], "tests/test_foo.py")],
    )
    report = await detect_missing_tests(async_session, repo.id, {"src/foo.py": {2, 99}})

    # A partial hit still counts as covered (some test touches the change), so
    # this is a stale-test candidate, not an untested change.
    assert report.untested_changes == []
    assert len(report.stale_test_candidates) == 1


async def test_a_truncated_map_reads_missing_tests_as_unknown(async_session, monkeypatch):
    # At the row cap whole tests were left out, so neither an uncovered change
    # nor an unedited covering test proves anything: both files are unknown.
    from repowise.core.persistence.crud.analysis import coverage_map

    repo = await _seed(
        async_session,
        [
            _rec("tests/test_foo.py::test_a", "src/foo.py", [1, 2], "tests/test_foo.py"),
            _rec("tests/test_bar.py::test_b", "src/bar.py", [5], "tests/test_bar.py"),
        ],
    )
    monkeypatch.setattr(coverage_map, "MAX_TEST_COVERAGE_ROWS", 2)

    report = await detect_missing_tests(
        async_session, repo.id, {"src/foo.py": {99}, "src/bar.py": {5}}
    )

    assert report.map_truncated is True
    assert report.untested_changes == []
    assert report.stale_test_candidates == []
    assert sorted(report.no_data) == ["src/bar.py", "src/foo.py"]
    assert report.has_signal() is False


async def test_every_outcome_in_one_change_reads_each_file_its_own_rows(async_session):
    # One change touching a file per outcome: the rows are read for the whole
    # change at once (#3345), so no file may be judged on another file's rows.
    repo = await _seed(
        async_session,
        [
            _rec("tests/test_a.py::test_a", "src/untested.py", [1, 2], "tests/test_a.py"),
            _rec("tests/test_b.py::test_b", "src/stale.py", [5, 6], "tests/test_b.py"),
            _rec("tests/test_c.py::test_c", "src/covered.py", [7], "tests/test_c.py"),
            _rec("pathless.test_d", "src/pathless.py", [3], None),
        ],
    )
    report = await detect_missing_tests(
        async_session,
        repo.id,
        {
            "src/untested.py": {5},
            "src/stale.py": {6},
            "src/covered.py": {7},
            "tests/test_c.py": {1},
            "src/pathless.py": {3},
            "src/unknown.py": {1},
        },
    )

    assert [(u.source_file, u.uncovered_lines) for u in report.untested_changes] == [
        ("src/untested.py", [5])
    ]
    assert [(s.source_file, s.covering_test_files) for s in report.stale_test_candidates] == [
        ("src/stale.py", ["tests/test_b.py"])
    ]
    assert report.covered == ["src/covered.py", "src/pathless.py"]
    assert report.no_data == ["src/unknown.py", "tests/test_c.py"]
