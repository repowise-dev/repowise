"""The token cache writes a small delta beside its base when few entries move.

The delta must load back to exactly the entries a rewrite of the whole cache
would hold, a full run's included; a delta that does not load or extends
another base must leave the cache cold so the next run rebuilds it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.health.duplication import detect_clones
from repowise.core.analysis.health.duplication import token_cache as token_cache_mod
from repowise.core.analysis.health.duplication.token_cache import (
    _CACHE_FILENAME,
    _DELTA_FILENAME,
    DuplicationTokenCache,
    persisted_bytes,
)
from repowise.core.cache_seal import dump_sealed_pickle, load_sealed_pickle

from .test_duplication_incremental import (
    _BODY,
    _OTHER,
    LIMITS,
    MIN_LINES,
    WINDOW,
    _incremental,
    _key,
    _parsed,
    _seed,
)

# Fewer than one window of tokens: a full run caches it, but it never survives.
_TINY = "x = 1\n"


def _entries(cache_dir: Path) -> dict:
    cache = DuplicationTokenCache(cache_dir, WINDOW)
    cache.load()
    return cache._entries


def _full_run_of(tmp_path: Path, step: int):
    """A cached full run over a copy of the current tree: the report and its cache."""
    oracle = tmp_path / f"oracle{step}"
    oracle.mkdir()
    for path in tmp_path.glob("*.py"):
        (oracle / path.name).write_bytes(path.read_bytes())
    report = detect_clones(
        _parsed(oracle),
        window_tokens=WINDOW,
        min_lines=MIN_LINES,
        limits=LIMITS,
        cache_dir=oracle / ".repowise",
    )
    return report, _entries(oracle / ".repowise")


def test_chained_splices_write_deltas_that_load_as_a_full_run(tmp_path: Path, monkeypatch):
    """Add, edit, delete and rename splices leave the base untouched and the
    loaded entries equal to a full run's cache, with the same report."""
    monkeypatch.setattr(token_cache_mod, "_COMPACT_SHARE", 10.0)
    cache_dir = _seed(
        tmp_path,
        {"a.py": _BODY, "b.py": _BODY, "c.py": _OTHER, "d.py": _OTHER, "t.py": _TINY},
    )
    base_bytes = (cache_dir / _CACHE_FILENAME).read_bytes()
    assert not (cache_dir / _DELTA_FILENAME).exists()
    steps = [
        ({"e.py": _BODY.replace("doit", "added")}, set(), {"e.py"}),
        ({"a.py": _OTHER.replace("other", "edited")}, set(), {"a.py"}),
        ({}, {"b.py"}, set()),
        ({"r.py": (tmp_path / "c.py").read_text()}, {"c.py"}, {"r.py"}),
        ({"t.py": _BODY.replace("doit", "grown")}, set(), {"t.py"}),
    ]
    for step, (writes, deletes, changed) in enumerate(steps):
        for rel, body in writes.items():
            (tmp_path / rel).write_text(body)
        for rel in deletes:
            (tmp_path / rel).unlink()
        report = _incremental(_parsed(tmp_path), cache_dir, changed)
        assert report.diagnostics.get("incremental") is True

        oracle_report, oracle_entries = _full_run_of(tmp_path, step)
        assert _key(report) == _key(oracle_report)
        assert (cache_dir / _DELTA_FILENAME).exists()
        assert (cache_dir / _CACHE_FILENAME).read_bytes() == base_bytes
        assert _entries(cache_dir) == oracle_entries


def test_a_delta_past_the_share_rewrites_the_base(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(token_cache_mod, "_COMPACT_SHARE", 10.0)
    cache_dir = _seed(tmp_path, {"a.py": _BODY, "b.py": _BODY, "c.py": _OTHER})
    (tmp_path / "a.py").write_text(_OTHER.replace("other", "edited"))
    _incremental(_parsed(tmp_path), cache_dir, {"a.py"})
    assert (cache_dir / _DELTA_FILENAME).exists()

    monkeypatch.setattr(token_cache_mod, "_COMPACT_SHARE", 0.1)
    (tmp_path / "b.py").write_text(_OTHER.replace("other", "again"))
    report = _incremental(_parsed(tmp_path), cache_dir, {"b.py"})
    assert report.diagnostics.get("incremental") is True
    assert not (cache_dir / _DELTA_FILENAME).exists()
    assert _entries(cache_dir) == _full_run_of(tmp_path, 0)[1]


def test_a_no_op_run_writes_nothing(tmp_path: Path):
    cache_dir = _seed(tmp_path, {"a.py": _BODY, "b.py": _BODY})
    before = (cache_dir / _CACHE_FILENAME).stat().st_mtime_ns
    _incremental(_parsed(tmp_path), cache_dir, set())
    assert not (cache_dir / _DELTA_FILENAME).exists()
    assert (cache_dir / _CACHE_FILENAME).stat().st_mtime_ns == before


def _seed_with_delta(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(token_cache_mod, "_COMPACT_SHARE", 10.0)
    cache_dir = _seed(tmp_path, {"a.py": _BODY, "b.py": _BODY, "c.py": _OTHER})
    (tmp_path / "a.py").write_text(_OTHER.replace("other", "edited"))
    _incremental(_parsed(tmp_path), cache_dir, {"a.py"})
    assert (cache_dir / _DELTA_FILENAME).exists()
    return cache_dir


def _rebuilds_in_full(tmp_path: Path, cache_dir: Path) -> None:
    """The cache loads cold, and the next update re-detects and matches a full run."""
    assert _entries(cache_dir) == {}
    (tmp_path / "b.py").write_text(_BODY.replace("doit", "later"))
    report = _incremental(_parsed(tmp_path), cache_dir, {"b.py"})
    assert report.diagnostics.get("incremental") is not True
    oracle_report, oracle_entries = _full_run_of(tmp_path, 9)
    assert _key(report) == _key(oracle_report)
    assert not (cache_dir / _DELTA_FILENAME).exists()
    assert _entries(cache_dir) == oracle_entries


@pytest.mark.parametrize("damage", ["truncated", "garbage"])
def test_a_corrupt_delta_rebuilds_in_full(tmp_path: Path, monkeypatch, damage):
    cache_dir = _seed_with_delta(tmp_path, monkeypatch)
    delta = cache_dir / _DELTA_FILENAME
    raw = delta.read_bytes()
    delta.write_bytes(raw[: len(raw) // 2] if damage == "truncated" else b"not a delta")
    _rebuilds_in_full(tmp_path, cache_dir)


def test_a_delta_of_another_base_rebuilds_in_full(tmp_path: Path, monkeypatch):
    cache_dir = _seed_with_delta(tmp_path, monkeypatch)
    delta = cache_dir / _DELTA_FILENAME
    payload = load_sealed_pickle(delta, domain=_DELTA_FILENAME)
    payload["base_id"] = "0" * 16
    dump_sealed_pickle(delta, payload, domain=_DELTA_FILENAME)
    _rebuilds_in_full(tmp_path, cache_dir)


def test_a_base_without_an_id_still_serves_and_is_rewritten_with_one(tmp_path: Path):
    """A cache written before deltas keeps its entries; its first save rewrites it."""
    cache_dir = _seed(tmp_path, {"a.py": _BODY, "b.py": _BODY, "c.py": _OTHER})
    base = cache_dir / _CACHE_FILENAME
    payload = load_sealed_pickle(base, domain=_CACHE_FILENAME)
    del payload["base_id"]
    dump_sealed_pickle(base, payload, domain=_CACHE_FILENAME)
    assert _entries(cache_dir) == payload["files"]

    (tmp_path / "a.py").write_text(_OTHER.replace("other", "edited"))
    report = _incremental(_parsed(tmp_path), cache_dir, {"a.py"})
    assert report.diagnostics.get("incremental") is True
    assert not (cache_dir / _DELTA_FILENAME).exists()
    assert "base_id" in load_sealed_pickle(base, domain=_CACHE_FILENAME)


def test_persisted_bytes_counts_the_delta(tmp_path: Path, monkeypatch):
    cache_dir = _seed_with_delta(tmp_path, monkeypatch)
    sizes = [(cache_dir / name).stat().st_size for name in (_CACHE_FILENAME, _DELTA_FILENAME)]
    assert persisted_bytes(cache_dir) == sum(sizes)
