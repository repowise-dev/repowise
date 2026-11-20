"""CRUD for the per-test coverage map (``test_coverage_sources`` + ``test_coverage_tests``).

Module named ``coverage_map`` (not ``test_coverage``) so pytest does not
collect it as a test module.


The reverse index - "given a changed source file and lines, which tests hit
them" - is :func:`tests_covering`: one primary-key read per file, whose row
lists every covering test by number with its lines as ranges. Rows are
point-in-time (overwritten per ingest run), mirroring ``coverage_files``.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
import zlib
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.changed_lines import line_ranges

from ...models import TestCoverageSource, TestCoverageTest
from .._shared import _BATCH_SIZE, _now_utc

# Upper bound on ``(test, source file)`` pairs persisted per repo: a trust
# boundary, since the map is built from an uploaded report. Measured on this
# repository's own suite with per-test contexts: 24.3 pairs and 25 covered
# lines a pair per test, so a 50k-test suite is about 1.22M pairs (90 MB
# stored). The cap is 1.6x that. Whole tests are kept, so a truncated map
# stores at least this many pairs (at most one test's pairs more): readers
# take ``pair_count >= MAX_TEST_COVERAGE_ROWS`` as "truncated" and run
# everything rather than trust a map with tests missing.
MAX_TEST_COVERAGE_ROWS = 2_000_000

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

    # ``(test_id, source_file)`` pairs stored.
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

    Pure. A repeated ``(test_id, file_path)`` pair is merged, its covered
    lines unioned, before anything is counted against *max_rows*. Tests are
    then kept in a stable order (test file, then test id) while fewer than
    *max_rows* rows are kept; the test that reaches the cap is kept whole,
    every later one is dropped whole. A partial test would look measured and
    answer "these are all the tests" for its files.
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
    order = sorted(by_test, key=lambda t: (_test_file(by_test[t]), t))
    rows: list[Any] = []
    dropped: list[str] = []
    for test_id in order:
        if len(rows) >= max_rows:
            dropped.append(test_id)
        else:
            rows.extend(by_test[test_id])
    return rows, merged, tuple(dropped)


def _test_file(recs: list[Any]) -> str:
    """A test's file: the first named one, whatever order its records came in."""
    return min((r.test_file for r in recs if r.test_file), default="")


def _encode_lines(lines: Any) -> str:
    """Line numbers as inclusive ranges: ``[1, 2, 3, 7]`` -> ``"1-3,7"``."""
    return ",".join(str(lo) if lo == hi else f"{lo}-{hi}" for lo, hi in line_ranges(lines or ()))


def _decode_lines(text: str) -> list[int]:
    """The lines :func:`_encode_lines` wrote; a malformed part costs only itself."""
    out: list[int] = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        try:
            out.extend(range(int(lo), int(hi or lo) + 1))
        except ValueError:
            continue
    return out


def _spans(text: str) -> list[tuple[int, int]]:
    """The inclusive ``(lo, hi)`` spans of :func:`_encode_lines` output *text*."""
    out: list[tuple[int, int]] = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        try:
            out.append((int(lo), int(hi or lo)))
        except ValueError:
            continue
    return out


def _lines_in_spans(sorted_lines: list[int], text: str) -> list[int]:
    """The lines of *sorted_lines* inside *text*'s spans, each span parsed once."""
    hit: set[int] = set()
    for lo, hi in _spans(text):
        start = bisect_left(sorted_lines, lo)
        hit.update(sorted_lines[start : bisect_right(sorted_lines, hi)])
    return sorted(hit)


def _decode_tests(tests_json: str) -> list[tuple[int, str]]:
    """``(test_no, ranges)`` pairs of a :class:`TestCoverageSource` row."""
    try:
        return [(int(no), str(ranges)) for no, ranges in json.loads(tests_json)]
    except (ValueError, TypeError):
        return []


async def save_test_coverage(
    session: AsyncSession,
    repository_id: str,
    records: list[Any],
    *,
    source_format: str,
    ingested_commit_sha: str | None = None,
    max_rows: int = MAX_TEST_COVERAGE_ROWS,
) -> TestMapWrite:
    """Replace the test-coverage map for *repository_id* with *records*.

    *records* are resolved ``TestCoverage`` dataclasses (canonical
    ``file_path`` / ``test_file``), deduped and capped by :func:`cap_test_map`.
    One bulk delete per table, then batched bulk inserts: a row per test and
    a row per covered source file.
    """
    rows, merged, dropped = cap_test_map(records, max_rows)
    await session.execute(
        delete(TestCoverageSource).where(TestCoverageSource.repository_id == repository_id)
    )
    await session.execute(
        delete(TestCoverageTest).where(TestCoverageTest.repository_id == repository_id)
    )
    layout = _layout(rows, source_format)
    tests = [
        {
            "repository_id": repository_id,
            "test_no": no,
            "test_id": test_id,
            "test_file": test_file,
        }
        for no, (test_id, test_file) in enumerate(layout.tests)
    ]
    at = _now_utc()
    sources = [
        {
            "repository_id": repository_id,
            "source_file": source,
            "tests_json": tests_json,
            "test_count": count,
            "source_format": source_format,
            "ingested_at": at,
            "ingested_commit_sha": ingested_commit_sha,
        }
        for source, (count, tests_json) in layout.sources.items()
    ]
    for table, values in ((TestCoverageTest, tests), (TestCoverageSource, sources)):
        for i in range(0, len(values), _BULK_ROWS):
            await session.execute(insert(table.__table__), values[i : i + _BULK_ROWS])
    return TestMapWrite(written=len(rows), merged=merged, dropped_tests=dropped)


# --- The portable per-test map --------------------------------------------
#
# What ``repowise coverage export`` writes and a hosted upload carries: the
# stored layout itself, so a million-pair map travels as a few MB of gzip
# instead of a raw ``.coverage`` database that takes gigabytes to parse. Each
# source file's entry is its ``tests_json`` text exactly as stored, kept as
# text so a reader holds one string per file, never a Python object per pair.

#: ``format`` of a portable per-test map.
PER_TEST_MAP_FORMAT = "repowise-test-map-v1"

_GZIP_MAGIC = b"\x1f\x8b"
# A source's tests_json as the engine writes it: compact, non-empty,
# ``[[test_no,"1-4,9"],...]``.
# Line numbers stop at seven digits: no source file has ten million lines.
_LINE = r"\d{1,7}"
_RANGES = rf"{_LINE}(?:-{_LINE})?(?:,{_LINE}(?:-{_LINE})?)*"
# Test numbers stop at nine digits, well past the stored row cap.
_TEST_NO = r"\d{1,9}"
_ENTRIES_RE = re.compile(rf'\[\[{_TEST_NO},"{_RANGES}"\](?:,\[{_TEST_NO},"{_RANGES}"\])*\]')
_TEST_NO_RE = re.compile(rf'\[({_TEST_NO}),"')
_SPAN_RE = re.compile(r"(\d+)-(\d+)")
_JSON_COMPACT = (",", ":")


class PerTestMapError(ValueError):
    """Bytes that are not a portable per-test map, or expand past the limit."""


@dataclass(frozen=True)
class PerTestMap:
    """A per-test map in the stored layout, tests numbered densely from 0."""

    __test__ = False

    source_format: str
    # ``(test_id, test_file)`` by test number.
    tests: tuple[tuple[str, str | None], ...]
    # Source file -> ``(test_count, tests_json)``, ``tests_json`` being the
    # stored ``[[test_no, "1-4,9"], ...]`` text, sorted by test number.
    sources: dict[str, tuple[int, str]]
    ingested_commit_sha: str | None = None

    @property
    def pair_count(self) -> int:
        return sum(count for count, _ in self.sources.values())


def source_test_numbers(tmap: PerTestMap) -> dict[str, list[int]]:
    """The numbers of the tests that ran each source file of *tmap* (indexes into ``tests``)."""
    return {source: [no for no, _ in _decode_tests(text)] for source, (_, text) in tmap.sources.items()}


def _layout(rows: list[Any], source_format: str) -> PerTestMap:
    """Deduped records (:func:`cap_test_map` output) as a :class:`PerTestMap`.

    Tests are numbered in the order they first appear, which for capped rows
    is the stable test file, test id order.
    """
    by_test: dict[str, list[Any]] = {}
    for rec in rows:
        by_test.setdefault(rec.test_id, []).append(rec)
    test_no = {test_id: no for no, test_id in enumerate(by_test)}
    by_source: dict[str, list[tuple[int, str]]] = {}
    for rec in rows:
        by_source.setdefault(rec.file_path, []).append(
            (test_no[rec.test_id], _encode_lines(rec.covered_lines))
        )
    return PerTestMap(
        source_format=source_format,
        tests=tuple((t, _test_file(by_test[t]) or None) for t in test_no),
        sources={
            source: (len(entries), json.dumps(sorted(entries), separators=_JSON_COMPACT))
            for source, entries in by_source.items()
        },
    )


def per_test_map_from_records(records: list[Any], source_format: str) -> PerTestMap:
    """*records* (``TestCoverage``), repeated pairs merged, as a :class:`PerTestMap`."""
    rows, _, _ = cap_test_map(records, max_rows=sys.maxsize)
    return _layout(rows, source_format)


def _renumbered(
    tmap: PerTestMap,
    order: list[int],
    sources: dict[str, list[tuple[int, str]]],
    test_files: dict[int, str | None] | None = None,
) -> PerTestMap:
    """*tmap* with only the tests in *order*, renumbered by their place in it."""
    new_no = {old: new for new, old in enumerate(order)}
    out: dict[str, tuple[int, str]] = {}
    for source, entries in sources.items():
        kept = sorted((new_no[no], ranges) for no, ranges in entries if no in new_no)
        if kept:
            out[source] = (len(kept), json.dumps(kept, separators=_JSON_COMPACT))
    return replace(
        tmap,
        tests=tuple(
            (tmap.tests[no][0], tmap.tests[no][1] if test_files is None else test_files.get(no))
            for no in order
        ),
        sources=out,
    )


def cap_per_test_map(
    tmap: PerTestMap, max_rows: int = MAX_TEST_COVERAGE_ROWS
) -> tuple[PerTestMap, tuple[str, ...]]:
    """``(map, dropped_tests)``: :func:`cap_test_map`'s rule on a :class:`PerTestMap`.

    Tests in test file, test id order are kept while fewer than *max_rows*
    pairs are kept, the one that reaches the cap whole; later ones are dropped
    whole, so ``pair_count >= max_rows`` marks a cut map (:func:`map_is_truncated`).
    """
    if tmap.pair_count < max_rows:
        return tmap, ()
    decoded = {source: _decode_tests(text) for source, (_, text) in tmap.sources.items()}
    pairs = [0] * len(tmap.tests)
    for entries in decoded.values():
        for no, _ in entries:
            pairs[no] += 1
    order = sorted(range(len(tmap.tests)), key=lambda n: (tmap.tests[n][1] or "", tmap.tests[n][0]))
    kept: list[int] = []
    dropped: list[str] = []
    rows = 0
    for no in order:
        if rows >= max_rows:
            dropped.append(tmap.tests[no][0])
        else:
            kept.append(no)
            rows += pairs[no]
    if not dropped:
        return tmap, ()
    return _renumbered(tmap, kept, decoded), tuple(dropped)


def _union_ranges(texts: list[str]) -> str:
    """The union of range *texts*, merged span by span, never expanded to lines.

    Spans are what an untrusted map holds, so a wide one costs no memory here;
    reversed spans cover nothing and drop out.
    """
    spans = sorted(s for text in texts for s in _spans(text) if s[0] <= s[1])
    out: list[list[int]] = []
    for lo, hi in spans:
        if out and lo <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return ",".join(str(lo) if lo == hi else f"{lo}-{hi}" for lo, hi in out)


def remap_per_test_map(
    tmap: PerTestMap, source_paths: dict[str, str], test_paths: dict[str, str]
) -> PerTestMap:
    """*tmap* with its paths rewritten, for a map measured on another checkout.

    A source file missing from *source_paths* is dropped (no change query can
    name it), and two that land on one path merge, their lines unioned. A test
    file missing from *test_paths* becomes ``None``. Tests left covering
    nothing are dropped.
    """
    merged: dict[str, dict[int, list[str]]] = {}
    for source, (_, text) in tmap.sources.items():
        target = source_paths.get(source)
        if target is None:
            continue
        into = merged.setdefault(target, {})
        for no, ranges in _decode_tests(text):
            into.setdefault(no, []).append(ranges)
    sources = {
        target: [(no, u) for no, texts in by_no.items() if (u := _union_ranges(texts))]
        for target, by_no in merged.items()
    }
    used = sorted({no for entries in sources.values() for no, _ in entries})
    files = {no: test_paths.get(tmap.tests[no][1] or "") for no in used}
    return _renumbered(tmap, used, sources, files)


def dump_per_test_map(tmap: PerTestMap) -> bytes:
    """*tmap* as gzipped JSON (``format`` :data:`PER_TEST_MAP_FORMAT`)."""
    doc = {
        "format": PER_TEST_MAP_FORMAT,
        "source_format": tmap.source_format,
        "ingested_commit_sha": tmap.ingested_commit_sha,
        "tests": [list(t) for t in tmap.tests],
        "sources": {source: text for source, (_, text) in tmap.sources.items()},
    }
    return gzip.compress(json.dumps(doc, separators=_JSON_COMPACT).encode(), mtime=0)


def is_per_test_map(raw: bytes) -> bool:
    """Whether *raw* opens like a portable per-test map, gzipped or plain."""
    head = raw[:4096]
    if head.startswith(_GZIP_MAGIC):
        try:
            head = zlib.decompressobj(wbits=31).decompress(head, 512)
        except zlib.error:
            return False
    return head.lstrip()[:1] == b"{" and PER_TEST_MAP_FORMAT.encode() in head[:512]


def _gunzip(raw: bytes, max_bytes: int) -> bytes:
    """*raw* inflated, refusing past *max_bytes* without inflating further."""
    inflater = zlib.decompressobj(wbits=31)
    out = bytearray()
    try:
        chunk = inflater.decompress(raw, max_bytes + 1)
        out += chunk
        while inflater.unconsumed_tail and len(out) <= max_bytes:
            out += inflater.decompress(inflater.unconsumed_tail, max_bytes + 1 - len(out))
    except zlib.error as exc:
        raise PerTestMapError(f"not valid gzip: {exc}") from exc
    if len(out) > max_bytes:
        raise PerTestMapError(f"expands past {max_bytes} bytes")
    return bytes(out)


def _read_doc(raw: bytes, max_bytes: int) -> dict:
    """The JSON object in *raw* (gzipped or plain), at most *max_bytes* inflated."""
    if raw.startswith(_GZIP_MAGIC):
        raw = _gunzip(raw, max_bytes)
    elif len(raw) > max_bytes:
        raise PerTestMapError(f"larger than {max_bytes} bytes")
    try:
        doc = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise PerTestMapError("not JSON") from exc
    if not isinstance(doc, dict) or doc.get("format") != PER_TEST_MAP_FORMAT:
        raise PerTestMapError(f"format is not {PER_TEST_MAP_FORMAT}")
    return doc


def _is_test_item(item: Any) -> bool:
    return (
        isinstance(item, list)
        and len(item) == 2
        and isinstance(item[0], str)
        and bool(item[0])
        and (item[1] is None or isinstance(item[1], str))
    )


def _checked_tests(tests: Any) -> list[tuple[str, str | None]]:
    """``tests`` as ``(test_id, test_file)`` pairs, each one checked."""
    if not isinstance(tests, list):
        raise PerTestMapError("needs a `tests` list")
    if not all(_is_test_item(item) for item in tests):
        raise PerTestMapError("each test is [test_id, test_file or null]")
    return [(item[0], item[1] or None) for item in tests]


def _checked_sources(sources: Any, n_tests: int) -> tuple[dict[str, tuple[int, str]], set[int]]:
    """``(source -> (test_count, tests_json), test numbers used)``, each entry checked.

    The shape is checked whole by a pattern and the numbers read from it, never
    decoded entry by entry (a large map has a million entries).
    """
    if not isinstance(sources, dict):
        raise PerTestMapError("needs a `sources` object")
    checked: dict[str, tuple[int, str]] = {}
    used: set[int] = set()
    for source, text in sources.items():
        if not source or not isinstance(text, str) or not _ENTRIES_RE.fullmatch(text):
            raise PerTestMapError(f"tests of {str(source)[:200]} are not compact tests_json")
        numbers = [int(no) for no in _TEST_NO_RE.findall(text)]
        seen = set(numbers)
        if len(seen) < len(numbers) or max(numbers) >= n_tests:
            raise PerTestMapError(f"tests of {source[:200]} repeat or name unknown tests")
        if any(int(lo) > int(hi) for lo, hi in _SPAN_RE.findall(text)):
            raise PerTestMapError(f"tests of {source[:200]} hold a reversed line range")
        used |= seen
        checked[source] = (len(numbers), text)
    return checked, used


def _short_text(value: Any) -> str | None:
    return value[:64] if isinstance(value, str) and value else None


def load_per_test_map(raw: bytes, *, max_bytes: int) -> PerTestMap:
    """Read and check a portable per-test map from untrusted bytes.

    Gzipped or plain JSON, at most *max_bytes* once inflated. Every source
    entry is checked (test numbers in range, unique per file, ranges well
    formed) one file at a time. Raises :class:`PerTestMapError`.
    """
    doc = _read_doc(raw, max_bytes)
    tests = _checked_tests(doc.get("tests"))
    sources, used = _checked_sources(doc.get("sources"), len(tests))
    tmap = PerTestMap(
        source_format=_short_text(doc.get("source_format")) or "unknown",
        tests=tuple(tests),
        sources=sources,
        ingested_commit_sha=_short_text(doc.get("ingested_commit_sha")),
    )
    if len(used) < len(tests):
        # Tests covering nothing would count as measured; renumber them away.
        tmap = _renumbered(
            tmap, sorted(used), {s: _decode_tests(t) for s, (_, t) in sources.items()}
        )
    return tmap


async def get_per_test_map(session: AsyncSession, repository_id: str) -> PerTestMap | None:
    """The stored per-test map of *repository_id*, ``None`` when it has none."""
    rows = (
        (
            await session.execute(
                select(TestCoverageSource)
                .where(TestCoverageSource.repository_id == repository_id)
                .order_by(TestCoverageSource.source_file.asc())
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    tests = (
        await session.execute(
            select(TestCoverageTest.test_no, TestCoverageTest.test_id, TestCoverageTest.test_file)
            .where(TestCoverageTest.repository_id == repository_id)
            .order_by(TestCoverageTest.test_no.asc())
        )
    ).all()
    latest = max(rows, key=lambda r: r.ingested_at)
    # save_test_coverage numbers tests densely from 0, the numbering the
    # stored tests_json texts use; load_per_test_map refuses any other.
    return PerTestMap(
        source_format=latest.source_format or "unknown",
        tests=tuple((test_id, test_file) for _, test_id, test_file in tests),
        sources={r.source_file: (r.test_count, r.tests_json) for r in rows},
        ingested_commit_sha=latest.ingested_commit_sha,
    )


async def _source_rows(
    session: AsyncSession, repository_id: str, source_files: set[str]
) -> list[TestCoverageSource]:
    """The map rows of *source_files*, by file. SQLite caps the bound
    parameters per statement, so the set goes out in fixed-size pieces."""
    ordered = sorted(source_files)
    rows: list[TestCoverageSource] = []
    for start in range(0, len(ordered), _BATCH_SIZE):
        result = await session.execute(
            select(TestCoverageSource)
            .where(
                TestCoverageSource.repository_id == repository_id,
                TestCoverageSource.source_file.in_(ordered[start : start + _BATCH_SIZE]),
            )
            .order_by(TestCoverageSource.source_file.asc())
        )
        rows.extend(result.scalars().all())
    return rows


async def _tests_by_no(
    session: AsyncSession, repository_id: str, numbers: set[int]
) -> dict[int, tuple[str, str | None]]:
    """``{test_no: (test_id, test_file)}`` for *numbers*."""
    ordered = sorted(numbers)
    out: dict[int, tuple[str, str | None]] = {}
    for start in range(0, len(ordered), _BATCH_SIZE):
        result = await session.execute(
            select(
                TestCoverageTest.test_no, TestCoverageTest.test_id, TestCoverageTest.test_file
            ).where(
                TestCoverageTest.repository_id == repository_id,
                TestCoverageTest.test_no.in_(ordered[start : start + _BATCH_SIZE]),
            )
        )
        out.update({no: (test_id, test_file) for no, test_id, test_file in result.all()})
    return out


async def _covering(
    session: AsyncSession,
    repository_id: str,
    source_files: set[str],
    lines: set[int] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """``{source_file: [{test_id, test_file, covered_lines, source_format}]}``.

    With *lines*, a test is kept only when it ran one of them and its
    ``covered_lines`` narrow to those it ran. Each file's tests are ordered by
    test id, then test file.
    """
    if not source_files:
        return {}
    decoded: list[tuple[TestCoverageSource, list[tuple[int, list[int]]]]] = []
    numbers: set[int] = set()
    wanted = sorted(lines) if lines is not None else []
    for row in await _source_rows(session, repository_id, source_files):
        hits: list[tuple[int, list[int]]] = []
        for no, ranges in _decode_tests(row.tests_json):
            if lines is not None:
                # Ranges are checked as spans, never expanded to every line.
                covered = _lines_in_spans(wanted, ranges)
                if not covered:
                    continue
            else:
                covered = _decode_lines(ranges)
            hits.append((no, covered))
            numbers.add(no)
        decoded.append((row, hits))
    names = await _tests_by_no(session, repository_id, numbers)
    out: dict[str, list[dict[str, Any]]] = {}
    for row, hits in decoded:
        entries = [
            {
                "test_id": names[no][0],
                "test_file": names[no][1],
                "covered_lines": covered,
                "source_format": row.source_format,
            }
            for no, covered in hits
            if no in names
        ]
        if entries:
            entries.sort(key=lambda e: (e["test_id"], e["test_file"] or ""))
            out[row.source_file] = entries
    return out


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
    found = await _covering(session, repository_id, {source_file}, lines)
    return found.get(source_file, [])


async def tests_covering_files(
    session: AsyncSession,
    repository_id: str,
    source_files: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """Bulk reverse index for recommendation validation.

    Unlike :func:`tests_covering`, this reads the complete target set in
    bounded pieces. Callers may then apply plan-specific line ranges in
    memory without turning recommendation hydration into one query per plan.
    Buckets and their rows are ordered deterministically; ``covered_lines``
    remains complete so a caller can distinguish a measured line hit from a
    file-only match.
    """
    return await _covering(session, repository_id, source_files)


async def covered_source_files(
    session: AsyncSession,
    repository_id: str,
    source_files: set[str],
) -> dict[str, list[str]]:
    """The files among *source_files* with >=1 per-test coverage row, and their tests.

    A file with a row is coverage-*proven* to be exercised by some test - the
    honest basis for "this changed file is tested". A file with no row is
    "unknown" (the coverage run may simply not have exercised it), never a
    proof of absence, so callers fall back to a labelled filename guess for
    those rather than asserting a test gap. Each file maps to its covering
    test files, sorted; a test without a test file path is named by its test id.
    """
    if not source_files:
        return {}
    rows = await _source_rows(session, repository_id, source_files)
    numbers_by_source = {
        row.source_file: {no for no, _ in _decode_tests(row.tests_json)} for row in rows
    }
    names = await _tests_by_no(session, repository_id, set().union(*numbers_by_source.values()))
    out: dict[str, list[str]] = {}
    for source, numbers in numbers_by_source.items():
        tests = {names[no][1] or names[no][0] for no in numbers if no in names}
        if tests:
            out[source] = sorted(tests)
    return out


async def measured_test_files(session: AsyncSession, repository_id: str) -> set[str]:
    """The test files the per-test map measured: those with a row, covering anything.

    A test without a file path is named by the file part of its test id.
    """
    result = await session.execute(
        select(TestCoverageTest.test_file, TestCoverageTest.test_id).where(
            TestCoverageTest.repository_id == repository_id
        )
    )
    return {test_file or test_id.split("::", 1)[0] for test_file, test_id in result.all()}


async def get_test_coverage_summary(
    session: AsyncSession,
    repository_id: str,
) -> dict[str, Any]:
    """Repo-level shape of the map (counts + provenance). Empty when no rows."""
    pair_count, source_file_count = (
        await session.execute(
            select(
                func.coalesce(func.sum(TestCoverageSource.test_count), 0),
                func.count(),
            ).where(TestCoverageSource.repository_id == repository_id)
        )
    ).one()
    if not source_file_count:
        return {
            "pair_count": 0,
            "test_count": 0,
            "source_file_count": 0,
            "source_format": None,
            "ingested_at": None,
            "ingested_commit_sha": None,
        }
    test_count = (
        await session.execute(
            select(func.count()).where(TestCoverageTest.repository_id == repository_id)
        )
    ).scalar_one()
    latest = (
        await session.execute(
            select(
                TestCoverageSource.source_format,
                TestCoverageSource.ingested_at,
                TestCoverageSource.ingested_commit_sha,
            )
            .where(TestCoverageSource.repository_id == repository_id)
            .order_by(TestCoverageSource.ingested_at.desc())
            .limit(1)
        )
    ).one()
    return {
        "pair_count": int(pair_count),
        "test_count": test_count,
        "source_file_count": source_file_count,
        "source_format": latest.source_format,
        "ingested_at": latest.ingested_at,
        "ingested_commit_sha": latest.ingested_commit_sha,
    }
