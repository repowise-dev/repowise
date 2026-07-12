"""Per-test coverage readers + resolver (the test-to-code substrate).

Covers the two context-carrying sources (coverage.py ``.coverage`` sqlite
and lcov ``TN:`` records), their loud degradation when a report has no
contexts, and the resolver's reuse of the aggregate path matcher for both
the source path and the test's own file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from repowise.core.analysis.health.coverage import (
    parse_contexts_file,
    parse_coverage_sqlite,
    parse_lcov_contexts,
    resolve_test_reports,
)

# --- coverage.py sqlite fixture builders -----------------------------------


def _numbits(lines: list[int]) -> bytes:
    """Encode line numbers into coverage.py's numbits bitmap (line n -> bit n)."""
    if not lines:
        return b""
    buf = bytearray((max(lines) // 8) + 1)
    for n in lines:
        buf[n // 8] |= 1 << (n % 8)
    return bytes(buf)


def _write_coverage_db(
    path: Path,
    files: dict[int, str],
    contexts: dict[int, str],
    line_bits: list[tuple[int, int, list[int]]],
    *,
    with_arc_table: bool = True,
) -> None:
    """Write a minimal coverage.py-shaped sqlite db (line-mode)."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE file (id integer primary key, path text, unique(path));
        CREATE TABLE context (id integer primary key, context text, unique(context));
        CREATE TABLE line_bits (file_id integer, context_id integer, numbits blob,
            unique(file_id, context_id));
        """
    )
    if with_arc_table:
        conn.execute(
            "CREATE TABLE arc (file_id integer, context_id integer, fromno integer, "
            "tono integer, unique(file_id, context_id, fromno, tono))"
        )
    conn.executemany("INSERT INTO file (id, path) VALUES (?, ?)", files.items())
    conn.executemany("INSERT INTO context (id, context) VALUES (?, ?)", contexts.items())
    conn.executemany(
        "INSERT INTO line_bits (file_id, context_id, numbits) VALUES (?, ?, ?)",
        [(fid, cid, _numbits(lines)) for fid, cid, lines in line_bits],
    )
    conn.commit()
    conn.close()


def _write_arc_db(path: Path) -> None:
    """A branch/arc-mode db: no line_bits rows, covered lines live in arcs."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE file (id integer primary key, path text, unique(path));
        CREATE TABLE context (id integer primary key, context text, unique(context));
        CREATE TABLE line_bits (file_id integer, context_id integer, numbits blob,
            unique(file_id, context_id));
        CREATE TABLE arc (file_id integer, context_id integer, fromno integer,
            tono integer, unique(file_id, context_id, fromno, tono));
        """
    )
    conn.execute("INSERT INTO file (id, path) VALUES (1, 'src/mod.py')")
    conn.execute("INSERT INTO context (id, context) VALUES (1, 'tests/test_mod.py::test_a|run')")
    # Synthetic module entry (-1) and real line arcs 3->5, 5->7.
    conn.executemany(
        "INSERT INTO arc (file_id, context_id, fromno, tono) VALUES (?, ?, ?, ?)",
        [(1, 1, -1, 3), (1, 1, 3, 5), (1, 1, 5, 7)],
    )
    conn.commit()
    conn.close()


# --- coverage.py sqlite reader ---------------------------------------------


def test_sqlite_emits_per_test_records(tmp_path: Path) -> None:
    db = tmp_path / ".coverage"
    _write_coverage_db(
        db,
        files={1: "src/foo.py", 2: "src/bar.py"},
        contexts={1: "", 2: "tests/test_foo.py::test_a|run", 3: "tests/test_foo.py::test_b|run"},
        line_bits=[
            (1, 2, [1, 3, 5]),  # test_a covers foo lines 1,3,5
            (2, 2, [10]),  # test_a also covers bar line 10
            (1, 3, [1, 2]),  # test_b covers foo lines 1,2
        ],
    )
    report = parse_coverage_sqlite(db)
    assert report.has_contexts is True
    assert report.source_format == "coverage.py"

    by_key = {(r.test_id, r.file_path): r for r in report.records}
    assert by_key[("tests/test_foo.py::test_a|run", "src/foo.py")].covered_lines == [1, 3, 5]
    assert by_key[("tests/test_foo.py::test_a|run", "src/bar.py")].covered_lines == [10]
    assert by_key[("tests/test_foo.py::test_b|run", "src/foo.py")].covered_lines == [1, 2]
    # The empty context (id 1) never produces a record.
    assert all(r.test_id for r in report.records)


def test_sqlite_without_contexts_degrades_loudly(tmp_path: Path) -> None:
    db = tmp_path / ".coverage"
    _write_coverage_db(
        db,
        files={1: "src/foo.py"},
        contexts={1: ""},  # ran without --contexts
        line_bits=[(1, 1, [1, 2, 3])],
    )
    report = parse_coverage_sqlite(db)
    assert report.has_contexts is False
    assert report.records == []


def test_sqlite_falls_back_to_arcs(tmp_path: Path) -> None:
    db = tmp_path / ".coverage"
    _write_arc_db(db)
    report = parse_coverage_sqlite(db)
    assert report.has_contexts is True
    rec = report.records[0]
    assert rec.file_path == "src/mod.py"
    # Synthetic -1 dropped; real endpoints 3,5,7 kept.
    assert rec.covered_lines == [3, 5, 7]


def test_sqlite_missing_db_degrades(tmp_path: Path) -> None:
    report = parse_coverage_sqlite(tmp_path / "does-not-exist.coverage")
    assert report.has_contexts is False
    assert report.records == []


def test_non_sqlite_file_degrades(tmp_path: Path) -> None:
    junk = tmp_path / ".coverage"
    junk.write_text("i am not a database", encoding="utf-8")
    report = parse_coverage_sqlite(junk)
    assert report.has_contexts is False
    assert report.records == []


# --- lcov TN reader --------------------------------------------------------


def test_lcov_contexts_from_tn_records() -> None:
    text = (
        "TN:test_alpha\nSF:src/foo.py\nDA:1,1\nDA:2,0\nDA:3,1\nend_of_record\n"
        "TN:test_beta\nSF:src/bar.py\nDA:10,1\nend_of_record\n"
    )
    report = parse_lcov_contexts(text)
    assert report.has_contexts is True
    by_test = {r.test_id: r for r in report.records}
    assert by_test["test_alpha"].file_path == "src/foo.py"
    assert by_test["test_alpha"].covered_lines == [1, 3]  # line 2 had 0 hits
    assert by_test["test_beta"].covered_lines == [10]


def test_lcov_without_tn_degrades_loudly() -> None:
    # Aggregate lcov: blank/absent TN means no test dimension.
    text = "TN:\nSF:src/foo.py\nDA:1,1\nend_of_record\nSF:src/bar.py\nDA:2,1\nend_of_record\n"
    report = parse_lcov_contexts(text)
    assert report.has_contexts is False
    assert report.records == []


# --- sniffing dispatcher ---------------------------------------------------


def test_parse_contexts_file_sniffs_sqlite(tmp_path: Path) -> None:
    db = tmp_path / ".coverage"
    _write_coverage_db(
        db,
        files={1: "src/foo.py"},
        contexts={1: "tests/test_foo.py::test_a|run"},
        line_bits=[(1, 1, [1, 2])],
    )
    report = parse_contexts_file(db)
    assert report.source_format == "coverage.py"
    assert report.has_contexts is True


def test_parse_contexts_file_sniffs_lcov(tmp_path: Path) -> None:
    lcov = tmp_path / "per-test.lcov"
    lcov.write_text("TN:test_x\nSF:src/foo.py\nDA:1,1\nend_of_record\n", encoding="utf-8")
    report = parse_contexts_file(lcov)
    assert report.source_format == "lcov"
    assert report.has_contexts is True


# --- resolver reuse --------------------------------------------------------


def test_resolve_test_reports_maps_source_and_test_paths() -> None:
    text = (
        "TN:tests/test_foo.py::test_a|run\nSF:/abs/build/src/foo.py\n"
        "DA:1,1\nDA:3,1\nend_of_record\n"
    )
    report = parse_lcov_contexts(text)
    repo_keys = {"packages/app/src/foo.py", "packages/app/tests/test_foo.py"}
    resolved = resolve_test_reports(report, repo_keys)

    assert resolved.has_contexts is True
    assert resolved.matched == 1
    assert resolved.test_files_resolved == 1
    rec = resolved.records[0]
    assert rec.file_path == "packages/app/src/foo.py"  # suffix-resolved source
    assert rec.test_file == "packages/app/tests/test_foo.py"  # test path resolved too
    assert rec.covered_lines == [1, 3]


def test_resolve_test_reports_reports_unmatched() -> None:
    text = "TN:suite_only\nSF:vendor/unknown.py\nDA:1,1\nend_of_record\n"
    report = parse_lcov_contexts(text)
    resolved = resolve_test_reports(report, {"src/foo.py"})
    assert resolved.records == []
    assert resolved.unmatched == ["vendor/unknown.py"]
    # A bare suite name (no path shape) leaves test_file unresolved.
    assert resolved.test_files_resolved == 0
