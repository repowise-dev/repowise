"""Stored coverage round-trips through the one row-to-model conversion.

``load_file_coverage`` feeds patch coverage, which needs the executable-line
set a report stated; ``load_coverage_map`` feeds health scoring in the shape
the report resolver builds, so stored and freshly parsed coverage score alike.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from repowise.core.analysis.health.coverage import (
    file_coverage,
    parse_lcov,
    resolve_reports,
)
from repowise.core.persistence.crud import (
    get_coverage_summary,
    load_coverage_for_repo,
    load_coverage_history,
    load_coverage_map,
    load_file_coverage,
    save_coverage_files,
    upsert_repository,
)
from repowise.core.persistence.models import CoverageIngest

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


async def test_ingest_provenance_reaches_the_summary_and_stored_patch_scope(
    async_session, repo
) -> None:
    from repowise.core.analysis.patch_coverage import stored_patch_coverage
    from repowise.core.persistence.crud import get_coverage_summary

    lcov = _LCOV + "SF:/elsewhere/gone.py\nDA:1,1\nend_of_record\n"
    resolved = resolve_reports([parse_lcov(lcov)], {"src/a.py"})
    await save_coverage_files(
        async_session,
        repo.id,
        resolved.files,
        source_format="lcov",
        ingested_commit_sha="abc",
        provenance=resolved.provenance,
    )

    summary = await get_coverage_summary(async_session, repo.id, reference_commit="def")
    assert summary["source_formats"] == ["lcov"]
    assert summary["report_paths"] == {
        "total": 2,
        "matched": 1,
        "unmatched": 1,
        "ambiguous": 0,
        "unmatched_sample": ["/elsewhere/gone.py"],
    }
    assert summary["freshness"] == {"status": "stale", "indexed_commit": "def"}

    stored = await stored_patch_coverage(async_session, repo.id, {"src/a.py": {1}})
    assert stored is not None
    assert stored.scope.report_path_count == 2
    assert stored.scope.unmatched_report_path_count == 1


async def test_an_ingest_without_provenance_leaves_path_counts_unknown(
    async_session, repo
) -> None:
    from repowise.core.persistence.crud import get_coverage_summary

    await save_coverage_files(
        async_session, repo.id, [file_coverage("a.py", [1], [1])], source_format="cobertura"
    )
    summary = await get_coverage_summary(async_session, repo.id)

    assert summary["report_paths"] is None
    assert summary["source_formats"] == ["cobertura"]
    assert summary["freshness"] == {"status": "unknown", "indexed_commit": None}


async def test_a_reingest_replaces_the_previous_provenance(async_session, repo) -> None:
    from repowise.core.persistence.crud import get_coverage_summary

    resolved = resolve_reports([parse_lcov(_LCOV)], {"src/a.py"})
    for commit in ("one", "two"):
        await save_coverage_files(
            async_session,
            repo.id,
            resolved.files,
            source_format="lcov",
            ingested_commit_sha=commit,
            provenance=resolved.provenance,
        )

    summary = await get_coverage_summary(async_session, repo.id, reference_commit="two")
    assert summary["freshness"]["status"] == "current"
    assert summary["report_paths"]["total"] == 1


async def test_summary_counts_covered_lines_exactly(async_session, repo) -> None:
    # 10,001 of 20,001 is 50.0025%, stored as 50.0; deriving the count back
    # from the percentage would say 10,000.
    from repowise.core.persistence.crud import get_coverage_summary

    fc = file_coverage("big.py", range(1, 10_002), range(1, 20_002))
    await save_coverage_files(async_session, repo.id, [fc], source_format="lcov")

    summary = await get_coverage_summary(async_session, repo.id)
    assert fc.line_coverage_pct == 50.0
    assert summary["covered_lines"] == 10_001


def _at(minute: int) -> datetime:
    return datetime(2026, 9, 1, 12, minute, tzinfo=UTC)


async def _ingest(session, repo_id: str, covered: int, minute: int, **kwargs) -> None:
    """One report of ten coverable lines with *covered* of them hit."""
    fc = file_coverage("a.py", range(1, covered + 1), range(1, 11))
    await save_coverage_files(
        session, repo_id, [fc], source_format="lcov", ingested_at=_at(minute), **kwargs
    )


async def test_every_ingest_is_kept_with_its_figures(async_session, repo) -> None:
    for minute, covered in enumerate((5, 7, 6)):
        await _ingest(async_session, repo.id, covered, minute, ingested_commit_sha=f"c{minute}")

    history = await load_coverage_history(async_session, repo.id)

    assert [(p["ingested_commit_sha"], p["line_coverage_pct"]) for p in history] == [
        ("c0", 50.0),
        ("c1", 70.0),
        ("c2", 60.0),
    ]
    # SQLite drops the offset, as it does for every stored coverage timestamp.
    assert history[0]["ingested_at"].startswith("2026-09-01T12:00:00")
    # The per-file rows are still replaced: only the latest report is stored.
    assert len(await load_coverage_for_repo(async_session, repo.id)) == 1


async def test_ingests_past_the_retention_are_pruned(async_session, repo, monkeypatch) -> None:
    from repowise.core.persistence.crud.analysis import coverage as coverage_crud

    monkeypatch.setattr(coverage_crud, "COVERAGE_HISTORY_RETENTION", 3)
    for minute in range(5):
        await _ingest(async_session, repo.id, minute + 1, minute)

    rows = (await async_session.execute(select(CoverageIngest))).scalars().all()
    assert sorted(r.line_coverage_pct for r in rows) == [30.0, 40.0, 50.0]


async def test_the_summary_reads_the_latest_ingest(async_session, repo) -> None:
    resolved = resolve_reports([parse_lcov(_LCOV)], {"src/a.py"})
    await save_coverage_files(
        async_session,
        repo.id,
        resolved.files,
        source_format="lcov",
        provenance=resolved.provenance,
        ingested_at=_at(0),
    )
    # The newer ingest carries no provenance, so the summary must say unknown.
    await _ingest(async_session, repo.id, 4, 1)

    summary = await get_coverage_summary(async_session, repo.id)

    assert summary["report_paths"] is None
    assert summary["line_coverage_pct"] == 40.0


async def test_the_stored_figures_match_the_summary(async_session, repo) -> None:
    resolved = resolve_reports([parse_lcov(_LCOV)], {"src/a.py"})
    await save_coverage_files(
        async_session, repo.id, resolved.files, source_format="lcov", ingested_at=_at(0)
    )

    summary = await get_coverage_summary(async_session, repo.id)
    ingest = (await async_session.execute(select(CoverageIngest))).scalar_one()

    assert (ingest.line_coverage_pct, ingest.branch_coverage_pct) == (
        summary["line_coverage_pct"],
        summary["branch_coverage_pct"],
    )
    assert (ingest.covered_lines, ingest.total_lines) == (
        summary["covered_lines"],
        summary["total_lines"],
    )


async def test_partial_ingests_stay_out_of_the_history(async_session, repo) -> None:
    from repowise.core.analysis.health.coverage.discovery import CoverageProvenance

    await _ingest(async_session, repo.id, 5, 0)
    await _ingest(async_session, repo.id, 9, 1, provenance=CoverageProvenance(mapping_partial=True))

    history = await load_coverage_history(async_session, repo.id)

    assert [p["line_coverage_pct"] for p in history] == [50.0]


async def test_reingesting_the_same_report_restamps_rather_than_appends(
    async_session, repo
) -> None:
    await _ingest(async_session, repo.id, 5, 0, ingested_commit_sha="one")
    await _ingest(async_session, repo.id, 6, 1, ingested_commit_sha="two")
    # An update that re-ingests, or a full re-index: the same figures again.
    await _ingest(async_session, repo.id, 6, 2, ingested_commit_sha="three")

    history = await load_coverage_history(async_session, repo.id)

    assert [(p["ingested_commit_sha"], p["line_coverage_pct"]) for p in history] == [
        ("one", 50.0),
        ("three", 60.0),
    ]
    assert history[-1]["ingested_at"].startswith("2026-09-01T12:02:00")


async def test_an_ingest_with_no_coverable_lines_stays_off_the_trend(
    async_session, repo
) -> None:
    await _ingest(async_session, repo.id, 5, 0)
    await save_coverage_files(
        async_session,
        repo.id,
        [file_coverage("empty.py", [], [])],
        source_format="lcov",
        ingested_at=_at(1),
    )

    ingests = (
        await async_session.execute(select(CoverageIngest).order_by(CoverageIngest.ingested_at))
    ).scalars()
    history = await load_coverage_history(async_session, repo.id)

    assert [i.line_coverage_pct for i in ingests] == [50.0, None]
    assert [p["line_coverage_pct"] for p in history] == [50.0]


# The table as migration 0080 created it, with one row allowed per repository.
_OLD_INGESTS_DDL = """
CREATE TABLE coverage_ingests (
    id VARCHAR(32) NOT NULL PRIMARY KEY,
    repository_id VARCHAR(32) NOT NULL REFERENCES repositories (id) ON DELETE CASCADE,
    source_formats_json TEXT NOT NULL DEFAULT '[]',
    report_path_count INTEGER,
    matched_path_count INTEGER,
    unmatched_path_count INTEGER,
    ambiguous_path_count INTEGER,
    unmatched_sample_json TEXT NOT NULL DEFAULT '[]',
    mapping_partial BOOLEAN NOT NULL DEFAULT 0,
    ingested_at DATETIME NOT NULL,
    ingested_commit_sha VARCHAR(40),
    CONSTRAINT uq_coverage_ingests UNIQUE (repository_id)
)
"""


async def test_a_store_that_still_has_the_one_row_constraint_keeps_working(
    async_engine, tmp_path
) -> None:
    """Local stores never run Alembic, and the reconciler never drops a constraint."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from repowise.core.persistence.database import init_db

    async with async_engine.begin() as conn:
        await conn.execute(text("DROP TABLE coverage_ingests"))
        await conn.execute(text(_OLD_INGESTS_DDL))
    await init_db(async_engine)  # the reconciler adds the new columns

    factory = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        repo = await upsert_repository(session, name="old", local_path=str(tmp_path))
        await _ingest(session, repo.id, 5, 0, ingested_commit_sha="one")
        await _ingest(session, repo.id, 8, 1, ingested_commit_sha="two")
        await session.commit()

        summary = await get_coverage_summary(session, repo.id, reference_commit="two")
        history = await load_coverage_history(session, repo.id)

    assert summary["freshness"]["status"] == "current"
    assert [(p["ingested_commit_sha"], p["line_coverage_pct"]) for p in history] == [
        ("two", 80.0)
    ]
