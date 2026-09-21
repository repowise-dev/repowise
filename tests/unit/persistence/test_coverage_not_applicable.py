"""Storing "nothing to cover" as unknown rather than as 0% (issue #2193).

A report record with no coverable lines parses to ``line_coverage_pct = None``.
The column has to hold that, the repo rollup has to step over it, and a SQLite
store written before the column was nullable has to keep ingesting — those
stores never run Alembic, so their ``NOT NULL`` is permanent.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from repowise.core.analysis.health.coverage.model import FileCoverage
from repowise.core.persistence.crud import (
    get_coverage_summary,
    load_coverage_for_repo,
    save_coverage_files,
    upsert_repository,
)

_FILES = [
    # Measured and found nothing coverable — a type-only module.
    FileCoverage(
        file_path="src/types.ts",
        line_coverage_pct=None,
        branch_coverage_pct=None,
        covered_lines=[],
        total_coverable_lines=0,
    ),
    # Measured and genuinely uncovered.
    FileCoverage(
        file_path="src/real.ts",
        line_coverage_pct=0.0,
        branch_coverage_pct=None,
        covered_lines=[],
        total_coverable_lines=2,
    ),
    FileCoverage(
        file_path="src/ok.ts",
        line_coverage_pct=100.0,
        branch_coverage_pct=None,
        covered_lines=[1, 2],
        total_coverable_lines=2,
    ),
]


@pytest.fixture
async def repo(async_session, tmp_path):
    r = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    return r


async def test_not_applicable_round_trips_as_null(async_session, repo) -> None:
    await save_coverage_files(async_session, repo.id, _FILES, source_format="lcov")
    rows = {r.file_path: r for r in await load_coverage_for_repo(async_session, repo.id)}

    assert rows["src/types.ts"].line_coverage_pct is None
    # The distinction the bug collapsed: both files below are still numbers.
    assert rows["src/real.ts"].line_coverage_pct == 0.0
    assert rows["src/ok.ts"].line_coverage_pct == 100.0


async def test_summary_steps_over_the_null_row_without_moving(async_session, repo) -> None:
    await save_coverage_files(async_session, repo.id, _FILES, source_format="lcov")
    summary = await get_coverage_summary(async_session, repo.id)

    # 2 of 4 coverable lines, and the not-applicable row weighed nothing on
    # either side of that ratio — as it did not when it was stored as 0.0.
    assert summary["covered_lines"] == 2
    assert summary["total_lines"] == 4
    assert summary["line_coverage_pct"] == 50.0
    # ``file_count`` is files with a measurable percentage.
    assert summary["file_count"] == 2


async def test_summary_is_unknown_when_nothing_is_measurable(async_session, repo) -> None:
    """A report of nothing but type files does not make the repo 0% covered."""
    await save_coverage_files(async_session, repo.id, [_FILES[0]], source_format="lcov")
    summary = await get_coverage_summary(async_session, repo.id)
    assert summary["line_coverage_pct"] is None


async def test_legacy_not_null_store_drops_the_row_instead_of_failing(async_session, repo) -> None:
    """A SQLite store from before the column was nullable keeps ingesting.

    ``init_db``'s reconciler is additive-only and local stores never run
    Alembic, so the ``NOT NULL`` survives every upgrade. Writing NULL into it
    would abort the whole ingest; the writer reflects the column and omits the
    not-applicable rows instead. An absent row and a NULL percentage read the
    same everywhere downstream, so the two store shapes agree on every
    question anyone asks.
    """
    await async_session.execute(text("DROP TABLE coverage_files"))
    await async_session.execute(
        text(
            """
            CREATE TABLE coverage_files (
                id VARCHAR(32) NOT NULL PRIMARY KEY,
                repository_id VARCHAR(32) NOT NULL,
                file_path TEXT NOT NULL,
                source_format VARCHAR(32) NOT NULL,
                line_coverage_pct FLOAT NOT NULL,
                branch_coverage_pct FLOAT,
                covered_lines_json TEXT NOT NULL,
                total_coverable_lines INTEGER NOT NULL,
                mapping_partial BOOLEAN NOT NULL,
                ingested_at DATETIME NOT NULL,
                ingested_commit_sha VARCHAR(40),
                CONSTRAINT uq_coverage_files UNIQUE (repository_id, file_path)
            )
            """
        )
    )
    await async_session.commit()

    await save_coverage_files(async_session, repo.id, _FILES, source_format="lcov")
    rows = {r.file_path: r for r in await load_coverage_for_repo(async_session, repo.id)}

    assert "src/types.ts" not in rows
    assert rows["src/real.ts"].line_coverage_pct == 0.0
    # And the repo-level answer is the one the nullable store gives.
    summary = await get_coverage_summary(async_session, repo.id)
    assert summary["line_coverage_pct"] == 50.0
    assert summary["file_count"] == 2
