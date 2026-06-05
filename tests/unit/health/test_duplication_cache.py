"""Cached duplication runs must reproduce the uncached report exactly."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from repowise.core.analysis.health.duplication import detect_clones
from repowise.core.analysis.health.duplication.token_cache import (
    _CACHE_FILENAME,
    DuplicationTokenCache,
)


def _pf(path: str, abs_path: str) -> SimpleNamespace:
    return SimpleNamespace(
        file_info=SimpleNamespace(path=path, abs_path=abs_path, language="python"), symbols=[]
    )


_BODY = "\n".join(
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


def _setup(tmp_path: Path) -> list[SimpleNamespace]:
    (tmp_path / "a.py").write_text(_BODY)
    (tmp_path / "b.py").write_text(_BODY.replace("doit", "renamed"))
    (tmp_path / "c.py").write_text("x = 1\n")
    return [
        _pf("a.py", str(tmp_path / "a.py")),
        _pf("b.py", str(tmp_path / "b.py")),
        _pf("c.py", str(tmp_path / "c.py")),
    ]


def _report_key(report):
    return (
        sorted(
            (p.file_a, p.file_b, p.a_start_line, p.a_end_line, p.b_start_line, p.b_end_line)
            for p in report.pairs
        ),
        report.duplication_pct,
    )


def test_cached_run_equals_uncached(tmp_path: Path):
    parsed = _setup(tmp_path)
    cache_dir = tmp_path / ".repowise"

    baseline = detect_clones(parsed, window_tokens=20, min_lines=4)
    first = detect_clones(parsed, window_tokens=20, min_lines=4, cache_dir=cache_dir)
    assert (cache_dir / _CACHE_FILENAME).exists()
    second = detect_clones(parsed, window_tokens=20, min_lines=4, cache_dir=cache_dir)

    assert _report_key(first) == _report_key(baseline)
    assert _report_key(second) == _report_key(baseline)


def test_second_run_hits_cache_and_edit_misses(tmp_path: Path):
    parsed = _setup(tmp_path)
    cache_dir = tmp_path / ".repowise"
    detect_clones(parsed, window_tokens=20, min_lines=4, cache_dir=cache_dir)

    cache = DuplicationTokenCache(cache_dir, 20)
    cache.load()
    import hashlib

    for pf in parsed:
        digest = hashlib.sha256(Path(pf.file_info.abs_path).read_bytes()).hexdigest()
        assert cache.get(digest) is not None

    # Edit one file -> its hash misses; the others still hit.
    (tmp_path / "a.py").write_text(_BODY.replace("doit", "changed"))
    fresh = DuplicationTokenCache(cache_dir, 20)
    fresh.load()
    a_digest = hashlib.sha256((tmp_path / "a.py").read_bytes()).hexdigest()
    assert fresh.get(a_digest) is None

    # And the changed-file run still produces the right pairs.
    report = detect_clones(parsed, window_tokens=20, min_lines=4, cache_dir=cache_dir)
    assert any({p.file_a, p.file_b} == {"a.py", "b.py"} for p in report.pairs)


def test_window_size_mismatch_invalidates(tmp_path: Path):
    parsed = _setup(tmp_path)
    cache_dir = tmp_path / ".repowise"
    detect_clones(parsed, window_tokens=20, min_lines=4, cache_dir=cache_dir)

    other = DuplicationTokenCache(cache_dir, 10)
    other.load()
    assert other._entries == {}


def test_corrupt_cache_degrades_to_full_run(tmp_path: Path):
    parsed = _setup(tmp_path)
    cache_dir = tmp_path / ".repowise"
    cache_dir.mkdir()
    (cache_dir / _CACHE_FILENAME).write_bytes(b"not a pickle")

    baseline = detect_clones(parsed, window_tokens=20, min_lines=4)
    report = detect_clones(parsed, window_tokens=20, min_lines=4, cache_dir=cache_dir)
    assert _report_key(report) == _report_key(baseline)
