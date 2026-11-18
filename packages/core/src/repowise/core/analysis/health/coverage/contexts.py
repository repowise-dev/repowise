"""Per-test coverage readers (the test-to-code substrate).

The standard parsers (:mod:`.lcov`, :mod:`.cobertura`, ...) collapse every
test into one hit-wins aggregate per file - they know a file is covered,
never by *which* test. These readers keep the test dimension instead,
emitting one :class:`TestCoverage` per ``(test, file)`` pair.

Three sources carry per-test data today:

- **coverage.py ``.coverage`` sqlite** with per-test contexts (static or
  dynamic). The ``context`` table names each test; the
  ``line_bits`` / ``arc`` rows are keyed by ``(file_id, context_id)``.
- **lcov ``TN:`` records** - the test-name line, blank in most merged lcov
  but populated by per-test lcov output.
- **Salesforce Apex JSON** (``sf apex run test --code-coverage``): its
  ``ApexCodeCoverage`` records, one per test method and class (:mod:`.apex`).

Both degrade loudly: a report produced *without* contexts (a plain lcov, or
a ``.coverage`` without contexts) yields ``records=[]`` and
``has_contexts=False`` so callers report "aggregate only, per-test features
unavailable" rather than silently emitting nothing.

These readers are opt-in and standalone: nothing in the default ingest path
calls them (that is Phase 1+). They only parse; persistence, the reverse
index, and any downstream feature live above this layer.
"""

from __future__ import annotations

import codecs
import sqlite3
from pathlib import Path

from .apex import parse_apex_contexts
from .detector import detect_format
from .lcov import _normalize_path
from .model import ContextCoverageReport, TestCoverage

#: The command the CLI recommends for a ``.coverage`` with per-test contexts.
#: Needs pytest-cov; ``--cov-context=test`` names each context after its test.
PER_TEST_COVERAGE_COMMAND = "pytest --cov --cov-context=test"

# coverage.py sqlite files start with the standard SQLite magic header.
_SQLITE_MAGIC = b"SQLite format 3\x00"

#: Why a ``.coverage`` gives no per-file coverage: its schema stores the lines
#: that ran, never the executable set, so no percentage can be taken from it.
COVERAGE_DATA_FILE_NOTE = (
    "a coverage.py data file records the lines that ran but not which lines are "
    "executable, so it gives no per-file coverage; export it with `coverage lcov` "
    "or `coverage xml` and add that report"
)


def is_coverage_data_file(path: str | Path) -> bool:
    """True when *path* is a coverage.py ``.coverage`` sqlite (binary magic)."""
    try:
        with Path(path).open("rb") as fh:
            return fh.read(len(_SQLITE_MAGIC)) == _SQLITE_MAGIC
    except OSError:
        return False


def read_report_text(path: str | Path) -> str:
    """A text report's contents: UTF-8, or UTF-16 when it opens with a UTF-16 BOM.

    Windows PowerShell 5.1 ``>`` redirection writes UTF-16. Raises like ``read_text``.
    """
    data = Path(path).read_bytes()
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16")
    return data.decode("utf-8")


def parse_lcov_contexts(text: str) -> ContextCoverageReport:
    """Parse per-test records from an lcov report's ``TN:`` names.

    Each ``end_of_record`` block carries at most one ``TN`` (test name) and
    one ``SF`` (source file); a covered ``DA`` line (hits > 0) becomes part
    of that test's line set. Blocks with a blank ``TN`` carry no test
    dimension and are skipped. If *no* block names a test, the report is
    aggregate-only and comes back with ``has_contexts=False``.
    """
    records: list[TestCoverage] = []
    has_contexts = False

    current_test: str | None = None
    current_path: str | None = None
    covered: set[int] = set()

    def flush() -> None:
        nonlocal current_test, current_path, covered, has_contexts
        if current_test and current_path and covered:
            has_contexts = True
            records.append(
                TestCoverage(
                    test_id=current_test,
                    file_path=current_path,
                    covered_lines=sorted(covered),
                    source_format="lcov",
                )
            )
        current_test = None
        current_path = None
        covered = set()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == "end_of_record":
            flush()
            continue
        if ":" not in line:
            continue
        tag, _, rest = line.partition(":")
        if tag == "TN":
            name = rest.strip()
            if name:
                current_test = name
        elif tag == "SF":
            current_path = _normalize_path(rest)
        elif tag == "DA":
            parts = rest.split(",")
            if len(parts) >= 2:
                try:
                    line_no = int(parts[0])
                    hits = int(parts[1])
                except ValueError:
                    continue
                if hits > 0:
                    covered.add(line_no)

    # Some reports omit the final end_of_record.
    flush()
    return ContextCoverageReport(source_format="lcov", records=records, has_contexts=has_contexts)


def _numbits_to_lines(numbits: bytes) -> list[int]:
    """Decode a coverage.py ``numbits`` blob to the line numbers it sets.

    ``numbits`` is coverage.py's bitmap encoding: line number ``n`` is bit
    ``n`` (byte ``n // 8``, bit ``n % 8``). We decode inline rather than
    importing ``coverage.numbits`` so the reader has no runtime dependency
    on coverage.py being importable.
    """
    lines: list[int] = []
    for byte_i, byte in enumerate(numbits):
        if not byte:
            continue
        for bit_i in range(8):
            if (byte >> bit_i) & 1:
                lines.append(byte_i * 8 + bit_i)
    return lines


def parse_coverage_sqlite(db_path: str | Path) -> ContextCoverageReport:
    """Parse per-test records from a coverage.py ``.coverage`` sqlite file.

    Reads the ``file`` / ``context`` / ``line_bits`` tables (falling back to
    ``arc`` endpoints when the db was written in branch/arc mode). Only
    non-empty contexts become records; a db without contexts
    has a single empty context and comes back ``has_contexts=False``.
    """
    path = Path(db_path)
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        # Not openable as sqlite (e.g. an lcov file handed here by mistake).
        return ContextCoverageReport(source_format="coverage.py", has_contexts=False)

    try:
        contexts = {cid: ctx for cid, ctx in conn.execute("SELECT id, context FROM context")}
        named = {cid: ctx for cid, ctx in contexts.items() if ctx}
        if not named:
            # A .coverage without contexts: one empty context, aggregate only.
            return ContextCoverageReport(source_format="coverage.py", has_contexts=False)

        files = {fid: _normalize_path(p) for fid, p in conn.execute("SELECT id, path FROM file")}

        # (file_id, context_id) -> covered line set. line_bits is the common
        # (line-mode) source; arc rows fill in for branch-mode dbs.
        covered: dict[tuple[int, int], set[int]] = {}
        for file_id, ctx_id, numbits in conn.execute(
            "SELECT file_id, context_id, numbits FROM line_bits"
        ):
            if ctx_id not in named or file_id not in files:
                continue
            covered.setdefault((file_id, ctx_id), set()).update(_numbits_to_lines(numbits))

        if not covered and _has_table(conn, "arc"):
            for file_id, ctx_id, fromno, tono in conn.execute(
                "SELECT file_id, context_id, fromno, tono FROM arc"
            ):
                if ctx_id not in named or file_id not in files:
                    continue
                lines = covered.setdefault((file_id, ctx_id), set())
                # Arc endpoints are line numbers; <= 0 are synthetic
                # (module entry/exit), not real lines.
                if fromno and fromno > 0:
                    lines.add(fromno)
                if tono and tono > 0:
                    lines.add(tono)
    except sqlite3.Error:
        return ContextCoverageReport(source_format="coverage.py", has_contexts=False)
    finally:
        conn.close()

    records = [
        TestCoverage(
            test_id=named[ctx_id],
            file_path=files[file_id],
            covered_lines=sorted(line_set),
            source_format="coverage.py",
        )
        for (file_id, ctx_id), line_set in covered.items()
        if line_set
    ]
    return ContextCoverageReport(
        source_format="coverage.py", records=records, has_contexts=bool(records)
    )


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def parse_contexts_file(path: str | Path) -> ContextCoverageReport:
    """Parse a per-test report: coverage.py sqlite, Salesforce Apex JSON, or lcov text.

    coverage.py ``.coverage`` files are sqlite (binary magic header); text the
    format sniffer calls Apex JSON is read for its per-test records; everything
    else is read as lcov text. Unreadable files degrade to an empty
    aggregate-only report rather than raising.
    """
    p = Path(path)
    if is_coverage_data_file(p):
        return parse_coverage_sqlite(p)
    try:
        text = read_report_text(p)
    except (OSError, UnicodeDecodeError):
        return ContextCoverageReport(source_format="unknown", has_contexts=False)
    if detect_format(text) == "apex":
        return parse_apex_contexts(text)
    return parse_lcov_contexts(text)
