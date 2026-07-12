"""CRUD for the per-test coverage map (``test_coverage`` table).

Module named ``coverage_map`` (not ``test_coverage``) so pytest does not
collect it as a test module.


The reverse index - "given a changed source file and lines, which tests hit
them" - is :func:`tests_covering`; the forward direction ("what does this
test cover") is :func:`files_covered_by`. Both are straight indexed queries
scoped to a repository. Rows are point-in-time (overwritten per ingest run),
mirroring ``coverage_files``.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import TestCoverageEntry, _new_uuid
from .._shared import _BATCH_SIZE

# Upper bound on rows persisted per repo, so a pathological O(tests x files)
# report can't balloon the table unbounded. Excess is dropped loudly (the
# caller compares ``written`` against the record count). Generous headroom:
# a 2000-test suite touching 100 files each is 200k pairs.
MAX_TEST_COVERAGE_ROWS = 250_000


async def save_test_coverage(
    session: AsyncSession,
    repository_id: str,
    records: list[Any],
    *,
    source_format: str,
    ingested_commit_sha: str | None = None,
    max_rows: int = MAX_TEST_COVERAGE_ROWS,
) -> int:
    """Replace the test-coverage rows for *repository_id* with *records*.

    *records* are resolved ``TestCoverage`` dataclasses (canonical
    ``file_path`` / ``test_file``). Delete-then-insert, capped at *max_rows*;
    returns the number of rows written so the caller can report truncation.
    """
    existing = await session.execute(
        select(TestCoverageEntry).where(TestCoverageEntry.repository_id == repository_id)
    )
    for row in existing.scalars().all():
        await session.delete(row)
    await session.flush()

    # Deduplicate on (test_id, source_file) - the unique key - keeping the
    # first occurrence, so a report that repeats a pair never trips the
    # constraint mid-batch.
    seen: set[tuple[str, str]] = set()
    capped = records[:max_rows]
    written = 0
    batch: list[TestCoverageEntry] = []
    for rec in capped:
        key = (rec.test_id, rec.file_path)
        if key in seen:
            continue
        seen.add(key)
        batch.append(
            TestCoverageEntry(
                id=_new_uuid(),
                repository_id=repository_id,
                test_id=rec.test_id,
                test_file=rec.test_file,
                source_file=rec.file_path,
                covered_lines_json=json.dumps(list(rec.covered_lines or [])),
                source_format=source_format,
                ingested_commit_sha=ingested_commit_sha,
            )
        )
        if len(batch) >= _BATCH_SIZE:
            session.add_all(batch)
            await session.flush()
            written += len(batch)
            batch = []
    if batch:
        session.add_all(batch)
        await session.flush()
        written += len(batch)
    return written


def _decode_lines(row: TestCoverageEntry) -> list[int]:
    try:
        return list(json.loads(row.covered_lines_json))
    except (ValueError, TypeError):
        return []


async def tests_covering(
    session: AsyncSession,
    repository_id: str,
    source_file: str,
    *,
    lines: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Reverse index: tests whose coverage touches *source_file*.

    When *lines* is given, only tests whose covered set intersects it are
    returned (the "changed lines -> impacted tests" query). Each result is
    ``{test_id, test_file, covered_lines, source_format}``, with
    ``covered_lines`` narrowed to the intersecting lines when *lines* is set.
    """
    result = await session.execute(
        select(TestCoverageEntry).where(
            TestCoverageEntry.repository_id == repository_id,
            TestCoverageEntry.source_file == source_file,
        )
    )
    out: list[dict[str, Any]] = []
    for row in result.scalars().all():
        covered = _decode_lines(row)
        if lines is not None:
            hit = sorted(lines.intersection(covered))
            if not hit:
                continue
            covered_out = hit
        else:
            covered_out = covered
        out.append(
            {
                "test_id": row.test_id,
                "test_file": row.test_file,
                "covered_lines": covered_out,
                "source_format": row.source_format,
            }
        )
    return out


async def files_covered_by(
    session: AsyncSession,
    repository_id: str,
    test_id: str,
) -> list[dict[str, Any]]:
    """Forward direction: source files (and lines) a given test covers."""
    result = await session.execute(
        select(TestCoverageEntry).where(
            TestCoverageEntry.repository_id == repository_id,
            TestCoverageEntry.test_id == test_id,
        )
    )
    return [
        {
            "source_file": row.source_file,
            "covered_lines": _decode_lines(row),
            "source_format": row.source_format,
        }
        for row in result.scalars().all()
    ]


async def get_test_coverage_summary(
    session: AsyncSession,
    repository_id: str,
) -> dict[str, Any]:
    """Repo-level shape of the map (counts + provenance). Empty when no rows."""
    result = await session.execute(
        select(TestCoverageEntry).where(TestCoverageEntry.repository_id == repository_id)
    )
    rows = list(result.scalars().all())
    if not rows:
        return {
            "pair_count": 0,
            "test_count": 0,
            "source_file_count": 0,
            "source_format": None,
            "ingested_at": None,
            "ingested_commit_sha": None,
        }
    latest = max(rows, key=lambda r: r.ingested_at)
    return {
        "pair_count": len(rows),
        "test_count": len({r.test_id for r in rows}),
        "source_file_count": len({r.source_file for r in rows}),
        "source_format": latest.source_format,
        "ingested_at": latest.ingested_at,
        "ingested_commit_sha": latest.ingested_commit_sha,
    }
