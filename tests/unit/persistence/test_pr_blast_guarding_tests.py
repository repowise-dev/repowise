"""Coverage-backed guarding tests in PRBlastRadiusAnalyzer._guarding_tests.

The positive complement of ``_find_test_gaps``: given the changed files, return
the tests the per-test coverage map proves execute them (the "run these to
validate" list). Seeds a ``test_coverage`` table and pins the map-present,
map-empty, and no-rows-for-file paths.
"""

from __future__ import annotations

from repowise.core.analysis.health.coverage import TestCoverage
from repowise.core.analysis.pr_blast import PRBlastRadiusAnalyzer
from repowise.core.persistence.crud import save_test_coverage
from tests.unit.persistence.helpers import insert_repo


async def _seed_map(session, repo_id):
    await save_test_coverage(
        session,
        repo_id,
        [
            TestCoverage(
                test_id="tests/test_math.py::test_add",
                file_path="src/math.py",
                covered_lines=[1, 2, 3],
                source_format="coverage.py",
                test_file="tests/test_math.py",
            ),
            TestCoverage(
                test_id="tests/test_math.py::test_sub",
                file_path="src/math.py",
                covered_lines=[5, 6],
                source_format="coverage.py",
                test_file="tests/test_math.py",
            ),
            TestCoverage(
                test_id="tests/test_util.py::test_helper",
                file_path="src/util.py",
                covered_lines=[10],
                source_format="coverage.py",
                test_file="tests/test_util.py",
            ),
        ],
        source_format="coverage.py",
    )


async def test_guarding_tests_returns_covering_tests_per_file(async_session):
    repo = await insert_repo(async_session)
    await _seed_map(async_session, repo.id)
    await async_session.commit()

    analyzer = PRBlastRadiusAnalyzer(async_session, repo.id)
    result = await analyzer._guarding_tests(["src/math.py"])

    assert result["map_present"] is True
    # Both math tests guard math.py; util's test does not leak in.
    assert result["tests_to_run"] == [
        "tests/test_math.py::test_add",
        "tests/test_math.py::test_sub",
    ]
    assert result["by_file"]["src/math.py"] == [
        "tests/test_math.py::test_add",
        "tests/test_math.py::test_sub",
    ]


async def test_guarding_tests_unions_and_dedups_across_changed_files(async_session):
    repo = await insert_repo(async_session)
    # test_shared guards BOTH files - must appear once in the union.
    await save_test_coverage(
        async_session,
        repo.id,
        [
            TestCoverage(
                test_id="tests/test_shared.py::test_both",
                file_path="src/a.py",
                covered_lines=[1],
                source_format="coverage.py",
                test_file="tests/test_shared.py",
            ),
            TestCoverage(
                test_id="tests/test_shared.py::test_both",
                file_path="src/b.py",
                covered_lines=[2],
                source_format="coverage.py",
                test_file="tests/test_shared.py",
            ),
        ],
        source_format="coverage.py",
    )
    await async_session.commit()

    analyzer = PRBlastRadiusAnalyzer(async_session, repo.id)
    result = await analyzer._guarding_tests(["src/a.py", "src/b.py"])

    assert result["tests_to_run"] == ["tests/test_shared.py::test_both"]
    assert set(result["by_file"]) == {"src/a.py", "src/b.py"}


async def test_guarding_tests_map_empty_is_honest_unknown(async_session):
    repo = await insert_repo(async_session)
    await async_session.commit()  # no coverage rows at all

    analyzer = PRBlastRadiusAnalyzer(async_session, repo.id)
    result = await analyzer._guarding_tests(["src/math.py"])

    # No map -> "unknown", never an empty-because-untested claim.
    assert result == {"map_present": False, "tests_to_run": [], "by_file": {}}


async def test_guarding_tests_file_with_no_rows_omitted_but_map_present(async_session):
    repo = await insert_repo(async_session)
    await _seed_map(async_session, repo.id)
    await async_session.commit()

    analyzer = PRBlastRadiusAnalyzer(async_session, repo.id)
    # new.py has no coverage rows; math.py does. Map IS present.
    result = await analyzer._guarding_tests(["src/new.py", "src/math.py"])

    assert result["map_present"] is True
    assert "src/new.py" not in result["by_file"]
    assert result["by_file"]["src/math.py"] == [
        "tests/test_math.py::test_add",
        "tests/test_math.py::test_sub",
    ]


async def test_guarding_tests_empty_changed_set(async_session):
    repo = await insert_repo(async_session)
    await _seed_map(async_session, repo.id)
    await async_session.commit()

    analyzer = PRBlastRadiusAnalyzer(async_session, repo.id)
    result = await analyzer._guarding_tests([])

    assert result == {"map_present": False, "tests_to_run": [], "by_file": {}}
