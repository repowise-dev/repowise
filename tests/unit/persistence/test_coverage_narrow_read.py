"""``load_coverage_for_repo(include_covered_lines=False)`` and the summary's
``rows=`` hand-off.

``covered_lines_json`` is most of what the coverage table stores — 467 KB of
549 KB on this codebase — and only the single-file detail view reads it. The
narrow read exists so the repo-wide callers stop hydrating it, and the rows it
returns have to stay attribute-compatible with the entities they replace,
because the serializers and the summary read them by name either way.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.crud import (
    get_coverage_summary,
    load_coverage_for_repo,
    save_coverage_files,
    upsert_repository,
)

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
        mapping_partial=True,
    )

    summary = await get_coverage_summary(async_session, r.id)
    assert summary["mapping_partial"] is True
    assert summary["file_count"] == 2
