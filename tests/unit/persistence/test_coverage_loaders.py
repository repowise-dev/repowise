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


async def test_stored_patch_coverage_matches_a_fresh_report(async_session, repo) -> None:
    from repowise.core.analysis.patch_coverage import (
        compute_patch_coverage,
        stored_patch_coverage,
    )

    fc = file_coverage("src/a.py", [1, 4], [1, 3, 4])
    other = file_coverage("src/b.py", [1], [1])
    await save_coverage_files(
        async_session, repo.id, [fc, other], source_format="lcov", ingested_commit_sha="abc"
    )
    changed = {"src/a.py": {1, 2, 3}, "src/new.py": {1}, "README.md": {1}}

    stored = await stored_patch_coverage(
        async_session, repo.id, changed, label="main...HEAD", head_commit="abc"
    )
    fresh = compute_patch_coverage(changed, {"src/a.py": fc, "src/b.py": other})

    assert stored is not None
    assert stored.files == fresh.files
    assert stored.out_of_scope_count == fresh.out_of_scope_count == 1
    assert stored.scope.freshness == "current"
    assert stored.scope.source_formats == ("lcov",)


async def test_stored_patch_coverage_marks_coverage_from_another_commit(
    async_session, repo
) -> None:
    from repowise.core.analysis.patch_coverage import render_markdown, stored_patch_coverage

    fc = file_coverage("src/a.py", [1], [1, 2])
    await save_coverage_files(
        async_session, repo.id, [fc], source_format="lcov", ingested_commit_sha="abcdef0123"
    )
    stored = await stored_patch_coverage(
        async_session, repo.id, {"src/a.py": {2}}, head_commit="fff"
    )

    assert stored is not None
    assert stored.scope.freshness == "stale"
    assert "measured at abcdef0, not at this change's head" in render_markdown(stored)


async def test_stored_patch_coverage_is_none_without_coverage(async_session, repo) -> None:
    from repowise.core.analysis.patch_coverage import stored_patch_coverage

    assert await stored_patch_coverage(async_session, repo.id, {"a.py": {1}}) is None
