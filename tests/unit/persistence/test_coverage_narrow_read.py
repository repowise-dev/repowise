"""``load_coverage_for_repo(include_covered_lines=False)`` and the summary's
``rows=`` hand-off.

``covered_lines_json`` is most of what the coverage table stores â€” 467 KB of
549 KB on this codebase â€” and only the single-file detail view reads it. The
narrow read exists so the repo-wide callers stop hydrating it, and the rows it
returns have to stay attribute-compatible with the entities they replace,
because the serializers and the summary read them by name either way.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.coverage import CoverageProvenance, TestCoverage
from repowise.core.persistence.crud import (
    get_coverage_summary,
    load_coverage_for_repo,
    save_coverage_files,
    save_test_coverage,
    upsert_repository,
)

# Aliased: pytest would otherwise collect the imported names as tests.
from repowise.core.persistence.crud import tests_covering as _tests_covering
from repowise.core.persistence.crud import tests_covering_many as _tests_covering_many

_FILES = [
    {"file_path": "src/a.py", "line_coverage_pct": 20.0, "covered_lines": [1, 2],
     "total_coverable_lines": 10, "branch_coverage_pct": 10.0},
    {"file_path": "src/b.py", "line_coverage_pct": 80.0, "covered_lines": [1, 2, 3, 4],
     "total_coverable_lines": 5, "branch_coverage_pct": None},
]

# Every field the route serializers and the summary read off a row.
_READ_BY_CALLERS = (
    "file_path",
    "source_format",
    "line_coverage_pct",
    "branch_coverage_pct",
    "total_coverable_lines",
    "ingested_at",
    "ingested_commit_sha",
)


@pytest.fixture
async def repo(async_session, tmp_path):
    r = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    await save_coverage_files(async_session, r.id, _FILES, source_format="lcov")
    return r


async def test_narrow_read_drops_the_blob_and_keeps_everything_else(
    async_session, repo
) -> None:
    narrow = await load_coverage_for_repo(
        async_session, repo.id, include_covered_lines=False
    )

    assert len(narrow) == 2
    for row in narrow:
        for field in _READ_BY_CALLERS:
            assert hasattr(row, field), field
        assert not hasattr(row, "covered_lines_json")


async def test_wide_read_still_carries_the_blob(async_session, repo) -> None:
    """The CLI's coverage-map builders depend on this being the default."""
    wide = await load_coverage_for_repo(async_session, repo.id)

    assert {r.file_path: r.covered_lines_json for r in wide} == {
        "src/a.py": "[1, 2]",
        "src/b.py": "[1, 2, 3, 4]",
    }


async def test_narrow_and_wide_agree_on_every_other_column(
    async_session, repo
) -> None:
    narrow = {r.file_path: r for r in
              await load_coverage_for_repo(async_session, repo.id,
                                           include_covered_lines=False)}
    wide = {r.file_path: r for r in await load_coverage_for_repo(async_session, repo.id)}

    assert narrow.keys() == wide.keys()
    for path, n in narrow.items():
        for field in _READ_BY_CALLERS:
            assert getattr(n, field) == getattr(wide[path], field), f"{path}.{field}"


async def test_narrow_read_honors_file_paths(async_session, repo) -> None:
    rows = await load_coverage_for_repo(
        async_session, repo.id, file_paths=["src/b.py"], include_covered_lines=False
    )

    assert [r.file_path for r in rows] == ["src/b.py"]


async def test_summary_from_handed_over_rows_matches_its_own_read(
    async_session, repo
) -> None:
    """The ``rows=`` hand-off must not change a single figure."""
    rows = await load_coverage_for_repo(
        async_session, repo.id, include_covered_lines=False
    )

    assert await get_coverage_summary(async_session, repo.id, rows=rows) == (
        await get_coverage_summary(async_session, repo.id)
    )


async def test_summary_weights_branch_coverage_over_rows_that_have_it(
    async_session, repo
) -> None:
    """Guards the narrow row's ``None`` branch column, which the weighting skips."""
    summary = await get_coverage_summary(async_session, repo.id)

    assert summary["file_count"] == 2
    # 2 covered of 10, plus 4 of 5.
    assert summary["covered_lines"] == 6
    assert summary["total_lines"] == 15
    assert summary["branch_coverage_pct"] == 10.0


async def test_summary_defaults_mapping_partial_false_for_legacy_ingests(
    async_session, repo
) -> None:
    """Rows written before the flag existed must read as complete, not partial."""
    summary = await get_coverage_summary(async_session, repo.id)

    assert summary["mapping_partial"] is False


async def _seed_per_test_map(session, repo_id: str) -> None:
    """Two files, four tests, overlapping and non-overlapping lines.

    ``src/a.py`` and ``src/b.py`` both have rows; ``src/none.py`` deliberately
    does not, so the batched read's empty bucket is exercised rather than assumed.
    """
    await save_test_coverage(
        session,
        repo_id,
        [
            TestCoverage(test_id="t1", file_path="src/a.py", covered_lines=[1, 2, 3]),
            TestCoverage(test_id="t2", file_path="src/a.py", covered_lines=[3, 4]),
            TestCoverage(test_id="t3", file_path="src/b.py", covered_lines=[10]),
            TestCoverage(test_id="t4", file_path="src/b.py", covered_lines=[11, 12]),
        ],
        source_format="coverage.py",
    )
    await session.commit()


@pytest.fixture
async def per_test_repo(async_session, tmp_path):
    r = await upsert_repository(async_session, name="pertest", local_path=str(tmp_path))
    await _seed_per_test_map(async_session, r.id)
    return r


async def test_batched_read_matches_one_call_per_file(async_session, per_test_repo):
    """The point of ``tests_covering_many``: same answers, one query.

    Three cases at once -- an unfiltered file, a line-filtered file, and a file
    with no rows at all -- because those are the three the callers construct.
    """
    repo_id = per_test_repo.id
    wanted = {"src/a.py": {2, 3}, "src/b.py": None, "src/none.py": {1}}

    batched = await _tests_covering_many(async_session, repo_id, wanted)

    for path, lines in wanted.items():
        per_file = await _tests_covering(async_session, repo_id, path, lines=lines)
        assert batched[path] == per_file, path


async def test_batched_read_keeps_a_file_with_no_rows_as_empty(
    async_session, per_test_repo
) -> None:
    """A missing file must be present and empty, not absent.

    The callers read the bucket to tell "no rows" from "not asked", so an absent
    key would be a different answer from ``tests_covering`` returning ``[]``.
    """
    batched = await _tests_covering_many(
        async_session, per_test_repo.id, {"src/none.py": None, "src/a.py": None}
    )

    assert batched["src/none.py"] == []
    assert "src/none.py" in batched
    assert len(batched["src/a.py"]) == 2


async def test_batched_read_on_an_empty_request_does_not_query(
    async_session, per_test_repo
) -> None:
    assert await _tests_covering_many(async_session, per_test_repo.id, {}) == {}


async def test_the_in_clause_chunk_is_under_the_sqlite_parameter_cap():
    """The piece size must stay below what the driver will actually accept.

    SQLite's SQLITE_LIMIT_VARIABLE_NUMBER is 32766 on 3.32+, and one bound
    parameter over it is an OperationalError rather than a slow query. Pinning the
    relationship means a later bump to the chunk size cannot silently cross the cap
    on the machine that runs the suite.
    """
    import sqlite3

    from repowise.core.persistence.batches import _IN_CLAUSE_CHUNK

    cap = sqlite3.connect(":memory:").getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
    assert cap > _IN_CLAUSE_CHUNK, (
        f"chunk of {_IN_CLAUSE_CHUNK} is at or over SQLite's {cap} bound-parameter cap"
    )
    # The batched read binds the repository id as well as the paths.
    assert cap >= _IN_CLAUSE_CHUNK + 1


async def test_batched_read_survives_a_path_list_over_the_sqlite_parameter_cap(
    async_session, tmp_path, monkeypatch
) -> None:
    """A change set wider than SQLite's bound-parameter cap still returns every file.

    This is the regression the previous body had: it put every path in one ``IN``
    list, so past the cap the whole read raised ``OperationalError: too many SQL
    variables`` instead of returning anything.

    The chunk size is lowered for this test so the cap is crossed with a handful of
    files. Building a real 32767-path repository allocates more rows than a unit
    test should, and the boundary being exercised -- more paths than one statement
    may bind -- is identical either way.
    """
    from repowise.core.persistence import batches

    small = 4
    monkeypatch.setattr(batches, "_IN_CLAUSE_CHUNK", small)

    r = await upsert_repository(async_session, name="wide", local_path=str(tmp_path))
    # Several pieces, so the read is split rather than issued whole. With the real
    # chunk size this is the 32766+ path change set the bot flagged.
    total = small * 5 + 3
    # Only some files have rows, so the result also proves an empty bucket survives
    # a chunk the file is not in.
    covered = {
        f"src/f{i:05d}.py": [i % 40 + 1, i % 40 + 2]
        for i in range(0, total, 3)
    }
    await save_test_coverage(
        async_session,
        r.id,
        [
            TestCoverage(test_id=f"t{i}", file_path=path, covered_lines=lines)
            for i, (path, lines) in enumerate(sorted(covered.items()))
        ],
        source_format="coverage.py",
    )
    await async_session.commit()

    wanted = {f"src/f{i:05d}.py": None for i in range(total)}
    batched = await _tests_covering_many(async_session, r.id, wanted)

    # Every requested path is answered, spanning all the pieces.
    assert set(batched) == set(wanted)
    assert len(batched) > small
    # Files with rows come back, and the ones straddling the boundary are intact.
    for path in covered:
        assert [row["test_id"] for row in batched[path]] == [
            row["test_id"] for row in await _tests_covering(async_session, r.id, path)
        ]
    # A file with no rows, in the last chunk, is still present and empty.
    assert batched[f"src/f{total - 1:05d}.py"] == []


async def test_batched_read_chunk_boundary_keeps_a_line_filtered_result(
    async_session, tmp_path
) -> None:
    """The line filter still applies inside a chunk, not just on a small set."""
    from repowise.core.persistence.batches import _IN_CLAUSE_CHUNK

    r = await upsert_repository(async_session, name="widefilter", local_path=str(tmp_path))
    total = _IN_CLAUSE_CHUNK + 3
    await save_test_coverage(
        async_session,
        r.id,
        [
            TestCoverage(
                test_id=f"t{i}", file_path=f"src/g{i:05d}.py", covered_lines=[1, 2, 3]
            )
            for i in range(total)
        ],
        source_format="coverage.py",
    )
    await async_session.commit()

    # Ask for line 3 on every file: the whole set is requested, so it spans chunks.
    wanted = {f"src/g{i:05d}.py": {3} for i in range(total)}
    batched = await _tests_covering_many(async_session, r.id, wanted)

    assert len(batched) == total
    assert all(rows for rows in batched.values())
    assert all(row["covered_lines"] == [3] for rows in batched.values() for row in rows)

    # And a line nothing covers drops the row in the wide case too.
    misses = {f"src/g{i:05d}.py": {99} for i in range(total)}
    assert await _tests_covering_many(async_session, r.id, misses) == {
        path: [] for path in misses
    }


async def test_summary_reports_mapping_partial_when_ingest_was_fragment(
    async_session, tmp_path
) -> None:
    """Issue #1746: a partial ingest stamps every row, and the summary surfaces
    it so consumers don't present the mapped subset as repository coverage."""
    r = await upsert_repository(async_session, name="partial", local_path=str(tmp_path))
    await save_coverage_files(
        async_session,
        r.id,
        _FILES,
        source_format="lcov",
        provenance=CoverageProvenance(mapping_partial=True),
    )

    summary = await get_coverage_summary(async_session, r.id)
    assert summary["mapping_partial"] is True
    assert summary["file_count"] == 2
