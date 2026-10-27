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
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import TestCoverageEntry, _new_uuid
from .._shared import _BATCH_SIZE, _now_utc

# Upper bound on rows persisted per repo, so a pathological O(tests x files)
# report can't balloon the table unbounded. Generous headroom: a 2000-test
# suite touching 100 files each is 200k pairs. Whole tests are kept, so a
# truncated map stores at least this many rows (at most one test's rows more):
# readers take ``pair_count >= MAX_TEST_COVERAGE_ROWS`` as "truncated" and run
# everything rather than trust a map with tests missing.
MAX_TEST_COVERAGE_ROWS = 250_000

#: Rows per bulk insert statement.
_BULK_ROWS = 5_000


def map_is_truncated(summary: dict[str, Any]) -> bool:
    """Whether a :func:`get_test_coverage_summary` describes a map cut at its row cap.

    A cut keeps whole tests up to and past the cap (:func:`cap_test_map`), so a
    truncated map stores at least the cap's rows. Its readers take a file's
    missing tests as unknown then, never as untested.
    """
    return summary.get("pair_count", 0) >= MAX_TEST_COVERAGE_ROWS


@dataclass(frozen=True)
class TestMapWrite:
    """What :func:`save_test_coverage` stored."""

    # Not a pytest test class despite the ``Test`` prefix.
    __test__ = False

    written: int
    # ``(test_id, source_file)`` pairs that repeated and were merged.
    merged: int = 0
    # Tests left out whole by the row cap, in the stable order they were cut.
    dropped_tests: tuple[str, ...] = ()

    @property
    def truncated(self) -> bool:
        return bool(self.dropped_tests)

    @property
    def dropped_sample(self) -> tuple[str, ...]:
        """The first few dropped tests, for a report; the count stays exact."""
        return self.dropped_tests[:5]


def cap_test_map(
    records: list[Any], max_rows: int = MAX_TEST_COVERAGE_ROWS
) -> tuple[list[Any], int, tuple[str, ...]]:
    """``(rows, merged, dropped_tests)``: *records* deduped, then capped by whole tests.

    Pure. A repeated ``(test_id, file_path)`` pair - the table's unique key -
    is merged, its covered lines unioned, before anything is counted against
    *max_rows*. Tests are then kept in a stable order (test file, then test
    id) while fewer than *max_rows* rows are kept; the test that reaches the
    cap is kept whole, every later one is dropped whole. A partial test would
    look measured and answer "these are all the tests" for its files.
    """
    pairs: dict[tuple[str, str], Any] = {}
    repeated: dict[tuple[str, str], set[int]] = {}
    for rec in records:
        key = (rec.test_id, rec.file_path)
        if key in pairs:
            repeated.setdefault(key, set(pairs[key].covered_lines or ())).update(
                rec.covered_lines or ()
            )
        else:
            pairs[key] = rec
    for key, lines in repeated.items():
        pairs[key] = replace(pairs[key], covered_lines=sorted(lines))
    merged = len(records) - len(pairs)
    by_test: dict[str, list[Any]] = {}
    for rec in pairs.values():
        by_test.setdefault(rec.test_id, []).append(rec)
    # A test's file sorts by its first named one, whatever order records came in.
    test_file = {
        t: min((r.test_file for r in recs if r.test_file), default="")
        for t, recs in by_test.items()
    }
    order = sorted(by_test, key=lambda t: (test_file[t], t))
    rows: list[Any] = []
    dropped: list[str] = []
    for test_id in order:
        if len(rows) >= max_rows:
            dropped.append(test_id)
        else:
            rows.extend(by_test[test_id])
    return rows, merged, tuple(dropped)


async def save_test_coverage(
    session: AsyncSession,
    repository_id: str,
    records: list[Any],
    *,
    source_format: str,
    ingested_commit_sha: str | None = None,
    max_rows: int = MAX_TEST_COVERAGE_ROWS,
) -> TestMapWrite:
    """Replace the test-coverage rows for *repository_id* with *records*.

    *records* are resolved ``TestCoverage`` dataclasses (canonical
    ``file_path`` / ``test_file``), deduped and capped by :func:`cap_test_map`.
    One bulk delete, then batched bulk inserts.
    """
    rows, merged, dropped = cap_test_map(records, max_rows)
    await session.execute(
        delete(TestCoverageEntry).where(TestCoverageEntry.repository_id == repository_id)
    )
    at = _now_utc()
    for i in range(0, len(rows), _BULK_ROWS):
        await session.execute(
            insert(TestCoverageEntry.__table__),
            [
                {
                    "id": _new_uuid(),
                    "repository_id": repository_id,
                    "test_id": rec.test_id,
                    "test_file": rec.test_file,
                    "source_file": rec.file_path,
                    "covered_lines_json": json.dumps(list(rec.covered_lines or [])),
                    "source_format": source_format,
                    "ingested_at": at,
                    "ingested_commit_sha": ingested_commit_sha,
                }
                for rec in rows[i : i + _BULK_ROWS]
            ],
        )
    return TestMapWrite(written=len(rows), merged=merged, dropped_tests=dropped)


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


async def tests_covering_files(
    session: AsyncSession,
    repository_id: str,
    source_files: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """Bulk reverse index for recommendation validation.

    Unlike :func:`tests_covering`, this performs indexed queries over the
    complete target set, one per bounded piece of it.  Callers may then apply
    plan-specific line ranges in
    memory without turning recommendation hydration into one query per plan.
    Rows and buckets are ordered deterministically; ``covered_lines`` remains
    complete so a caller can distinguish a measured line hit from a file-only
    match.
    """
    if not source_files:
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    # A full re-score asks for every file in the repository, and SQLite caps the
    # bound parameters per statement, so the set goes out in fixed-size pieces
    # (the same reason every other ``IN`` list here does). A file appears in
    # exactly one piece, so each file's rows keep their order.
    ordered = sorted(source_files)
    rows: list[TestCoverageEntry] = []
    for start in range(0, len(ordered), _BATCH_SIZE):
        result = await session.execute(
            select(TestCoverageEntry)
            .where(
                TestCoverageEntry.repository_id == repository_id,
                TestCoverageEntry.source_file.in_(ordered[start : start + _BATCH_SIZE]),
            )
            .order_by(
                TestCoverageEntry.source_file.asc(),
                TestCoverageEntry.test_id.asc(),
                TestCoverageEntry.test_file.asc(),
            )
        )
        rows.extend(result.scalars().all())
    for row in rows:
        out.setdefault(row.source_file, []).append(
            {
                "test_id": row.test_id,
                "test_file": row.test_file,
                "covered_lines": _decode_lines(row),
                "source_format": row.source_format,
            }
        )
    return out


async def covered_source_files(
    session: AsyncSession,
    repository_id: str,
    source_files: set[str],
) -> set[str]:
    """Return the subset of *source_files* that have >=1 per-test coverage row.

    A file with a row is coverage-*proven* to be exercised by some test - the
    honest basis for "this changed file is tested". A file with no row is
    "unknown" (the coverage run may simply not have exercised it), never a
    proof of absence, so callers fall back to a labelled filename guess for
    those rather than asserting a test gap.
    """
    if not source_files:
        return set()
    result = await session.execute(
        select(TestCoverageEntry.source_file).where(
            TestCoverageEntry.repository_id == repository_id,
            TestCoverageEntry.source_file.in_(source_files),
        )
    )
    return {row[0] for row in result.all()}


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
