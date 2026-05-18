"""Unit tests for the native Rabin-Karp duplication detector."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core.analysis.health.duplication import (
    DEFAULT_MIN_LINES,
    DEFAULT_WINDOW_TOKENS,
    detect_clones,
    tokenize_file,
)
from repowise.core.analysis.health.duplication.rabin_karp import (
    index_by_hash,
    rolling_hashes,
)
from repowise.core.analysis.health.duplication.tokenizer import Token


def _pf(path: str, abs_path: str, language: str = "python") -> SimpleNamespace:
    file_info = SimpleNamespace(path=path, abs_path=abs_path, language=language)
    return SimpleNamespace(file_info=file_info, symbols=[])


def _tok(kind: str, line: int = 1) -> Token:
    return Token(kind=kind, start_line=line, end_line=line, start_byte=0, end_byte=0)


def test_rolling_hash_matches_identical_streams():
    a = [_tok(k) for k in ["ID", "(", "ID", ",", "ID", ")", "ID"]]
    b = list(a)
    ha = rolling_hashes("a.py", a, window=4)
    hb = rolling_hashes("b.py", b, window=4)
    assert len(ha) == len(a) - 4 + 1
    assert {w.hash_value for w in ha} == {w.hash_value for w in hb}


def test_rolling_hash_window_too_large_returns_empty():
    a = [_tok("ID")] * 3
    assert rolling_hashes("a.py", a, window=10) == []


def test_index_by_hash_groups_collisions():
    a = [_tok(k) for k in ["ID"] * 10]
    h = rolling_hashes("a.py", a, window=4)
    bucket = index_by_hash(h)
    # All windows are identical → one bucket, multiple entries.
    assert len(bucket) == 1
    assert sum(len(v) for v in bucket.values()) == len(h)


def test_tokenize_file_drops_comments_and_normalizes_identifiers():
    source = b"def add(a, b):\n    # comment\n    return a + b + 42\n"
    toks = tokenize_file("python", source)
    kinds = [t.kind for t in toks]
    assert "ID" in kinds  # identifier collapsed
    assert "LIT" in kinds  # literal 42 collapsed
    # The comment text never appears.
    assert not any("comment" in k for k in kinds)


def _write(tmp_path: Path, name: str, src: str) -> Path:
    p = tmp_path / name
    p.write_text(src)
    return p


def test_detect_clones_finds_duplicate_function(tmp_path: Path):
    body = "\n".join(
        [
            "def doit(x, y, z):",
            "    if x:",
            "        a = x + y",
            "    else:",
            "        a = x - y",
            "    if z:",
            "        b = a * 2",
            "    else:",
            "        b = a - 1",
            "    return a + b + x + y + z",
            "",
        ]
    )
    src_a = body
    # Identical structure, renamed identifiers → must still match.
    src_b = body.replace("doit", "renamed").replace("x", "p").replace("y", "q")
    a = _write(tmp_path, "a.py", src_a)
    b = _write(tmp_path, "b.py", src_b)
    parsed = [
        _pf("a.py", str(a)),
        _pf("b.py", str(b)),
    ]
    report = detect_clones(parsed, window_tokens=20, min_lines=4)
    assert report.pairs, "expected at least one clone pair"
    pair = report.pairs[0]
    assert {pair.file_a, pair.file_b} == {"a.py", "b.py"}
    assert pair.a_line_count >= 4
    # No co-change history was provided → score stays 0.
    assert pair.co_change_count == 0
    # Duplication percentage should be populated for both files.
    assert "a.py" in report.duplication_pct
    assert "b.py" in report.duplication_pct


def test_detect_clones_attaches_co_change_count(tmp_path: Path):
    body = "\n".join(
        [
            "def doit(x, y, z):",
            "    total = 0",
            "    for i in range(10):",
            "        if i % 2:",
            "            total += i + x",
            "        else:",
            "            total -= i - y",
            "    return total + z",
            "",
        ]
    )
    a = _write(tmp_path, "a.py", body)
    b = _write(tmp_path, "b.py", body.replace("doit", "twin"))
    parsed = [_pf("a.py", str(a)), _pf("b.py", str(b))]
    git_meta_map = {
        "a.py": {
            "co_change_partners_json": json.dumps([{"file_path": "b.py", "co_change_count": 7}])
        },
        "b.py": {
            "co_change_partners_json": json.dumps([{"file_path": "a.py", "co_change_count": 5}])
        },
    }
    report = detect_clones(parsed, git_meta_map, window_tokens=20, min_lines=4)
    assert report.pairs
    # Max of the two reported directions wins.
    assert report.pairs[0].co_change_count == 7


def test_detect_clones_skips_files_without_duplicates(tmp_path: Path):
    a = _write(tmp_path, "a.py", "def f():\n    return 1\n")
    b = _write(tmp_path, "b.py", "def g():\n    return 2\n")
    parsed = [_pf("a.py", str(a)), _pf("b.py", str(b))]
    report = detect_clones(parsed, window_tokens=DEFAULT_WINDOW_TOKENS, min_lines=DEFAULT_MIN_LINES)
    assert report.pairs == []
    assert report.duplication_pct == {}


@pytest.mark.parametrize("language", ["python"])
def test_tokenize_file_returns_empty_for_unsupported_language(language: str):
    # An obviously invalid language code yields an empty stream rather
    # than raising.
    assert tokenize_file("not-a-language", b"x = 1\n") == []
