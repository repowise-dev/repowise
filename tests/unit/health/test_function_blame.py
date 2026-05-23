"""Unit tests for ``ingestion.git_indexer.function_blame``."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from repowise.core.ingestion.git_indexer.function_blame import (
    BlameIndex,
    _parse_porcelain,
    build_blame_index,
    distinct_commits_in_range,
    median_author_time_in_range,
    recent_commits_in_range,
)

_PORCELAIN = (
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 2\n"
    "author Alice\n"
    "author-time 1700000000\n"
    "summary first\n"
    "filename foo.py\n"
    "\tdef alpha():\n"
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 2 2\n"
    "\t    pass\n"
    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb 3 3 1\n"
    "author Bob\n"
    "author-time 1750000000\n"
    "summary second\n"
    "filename foo.py\n"
    "\tdef beta():\n"
    "cccccccccccccccccccccccccccccccccccccccc 5 4 1\n"
    "author Carol\n"
    "author-time 1755000000\n"
    "summary third\n"
    "filename foo.py\n"
    "\t    return 1\n"
)


def test_parse_porcelain_indexes_each_final_line():
    lines, authors = _parse_porcelain(_PORCELAIN)
    assert set(lines.keys()) == {1, 2, 3, 4}
    assert authors["a" * 40][0] == "Alice"
    assert authors["b" * 40][0] == "Bob"
    assert authors["c" * 40][0] == "Carol"
    assert lines[1] == ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 1700000000)
    # Second block reuses the same sha — author-time must propagate.
    assert lines[2] == ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 1700000000)
    assert lines[3] == ("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", 1750000000)
    assert lines[4] == ("cccccccccccccccccccccccccccccccccccccccc", 1755000000)


def test_distinct_commits_in_range():
    idx = BlameIndex(lines=_parse_porcelain(_PORCELAIN)[0])
    # Range 1-2 → single sha (a*).
    assert distinct_commits_in_range(idx, 1, 2) == {"a" * 40}
    # Range 1-4 → all three shas.
    assert distinct_commits_in_range(idx, 1, 4) == {"a" * 40, "b" * 40, "c" * 40}
    # Empty index → empty set.
    assert distinct_commits_in_range(BlameIndex(), 1, 10) == set()
    # Inverted range is a no-op.
    assert distinct_commits_in_range(idx, 4, 1) == set()


def test_median_author_time_in_range():
    idx = BlameIndex(lines=_parse_porcelain(_PORCELAIN)[0])
    # Lines 3-4 → median of (1750000000, 1755000000).
    assert median_author_time_in_range(idx, 3, 4) == (1750000000 + 1755000000) // 2
    # Single-line range → that line's timestamp.
    assert median_author_time_in_range(idx, 1, 1) == 1700000000
    # No coverage → None.
    assert median_author_time_in_range(idx, 100, 200) is None
    assert median_author_time_in_range(BlameIndex(), 1, 10) is None


def test_recent_commits_in_range():
    idx = BlameIndex(lines=_parse_porcelain(_PORCELAIN)[0])
    # since=1751000000 picks up only the c-sha line.
    assert recent_commits_in_range(idx, 1, 4, since_unix_ts=1751000000) == {"c" * 40}
    # since well in the past picks up everything.
    assert recent_commits_in_range(idx, 1, 4, since_unix_ts=0) == {
        "a" * 40,
        "b" * 40,
        "c" * 40,
    }


def test_build_blame_index_skips_low_commit_count():
    repo = SimpleNamespace(git=SimpleNamespace(blame=lambda *a, **kw: _PORCELAIN))
    idx = build_blame_index(repo, "foo.py", commit_count_total=2)
    assert idx.lines == {}


def test_build_blame_index_skips_oversize_file(tmp_path: Path):
    big = tmp_path / "big.py"
    big.write_bytes(b"x" * (200 * 1024))
    repo = SimpleNamespace(git=SimpleNamespace(blame=lambda *a, **kw: _PORCELAIN))
    idx = build_blame_index(
        repo,
        "big.py",
        repo_path=tmp_path,
        commit_count_total=50,
    )
    assert idx.lines == {}


def test_build_blame_index_returns_empty_on_blame_error():
    def _raise(*_a, **_kw):
        raise RuntimeError("git not available")

    repo = SimpleNamespace(git=SimpleNamespace(blame=_raise))
    idx = build_blame_index(repo, "foo.py", commit_count_total=50)
    assert idx.lines == {}


def test_build_blame_index_happy_path(tmp_path: Path):
    src = tmp_path / "foo.py"
    src.write_text("def alpha():\n    pass\n", encoding="utf-8")
    repo = SimpleNamespace(git=SimpleNamespace(blame=lambda *a, **kw: _PORCELAIN))
    idx = build_blame_index(
        repo,
        "foo.py",
        repo_path=tmp_path,
        commit_count_total=50,
    )
    assert set(idx.lines.keys()) == {1, 2, 3, 4}
