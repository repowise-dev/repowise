"""Stored coverage round-trips through the one row-to-model conversion.

``load_file_coverage`` feeds patch coverage, which needs the executable-line
set a report stated; ``load_coverage_map`` feeds health scoring in the shape
the report resolver builds, so stored and freshly parsed coverage score alike.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.coverage import (
    file_coverage,
    parse_lcov,
    resolve_reports,
)
from repowise.core.persistence.crud import (
    load_coverage_map,
    load_file_coverage,
    save_coverage_files,
    upsert_repository,
)

_LCOV = "SF:src/a.py\nDA:1,1\nDA:3,0\nDA:4,2\nBRDA:1,0,0,1\nBRDA:1,0,1,0\nend_of_record\n"


@pytest.fixture
async def repo(async_session, tmp_path):
    return await upsert_repository(async_session, name="repo", local_path=str(tmp_path))


async def test_executable_lines_survive_a_round_trip(async_session, repo) -> None:
    fc = file_coverage("src/a.py", [1, 4], [1, 3, 4], branches_found=2, branches_hit=1)
    await save_coverage_files(async_session, repo.id, [fc], source_format="lcov")

    loaded = await load_file_coverage(async_session, repo.id)

    assert loaded == {"src/a.py": fc}


async def test_stored_map_matches_the_resolved_map(async_session, repo) -> None:
    resolved = resolve_reports([parse_lcov(_LCOV)], {"src/a.py"})
    await save_coverage_files(
        async_session, repo.id, resolved.files, source_format=resolved.source_format
    )

    assert await load_coverage_map(async_session, repo.id) == resolved.coverage_map


async def test_rows_without_line_sets_load_as_unknown(async_session, repo) -> None:
    # A dict row with no line sets, as written before executable lines were stored.
    await save_coverage_files(
        async_session,
        repo.id,
        [{"file_path": "b.py", "line_coverage_pct": 50.0, "total_coverable_lines": 2}],
        source_format="cobertura",
    )

    (fc,) = (await load_file_coverage(async_session, repo.id)).values()
    assert fc.coverable_lines == []
    assert fc.covered_lines == []
