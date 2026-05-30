"""Unit tests for the per-function blame rollup (pure builder)."""

from __future__ import annotations

from dataclasses import dataclass

from repowise.core.analysis.health.function_blame_rollup import build_function_blame_rows
from repowise.core.ingestion.git_indexer.function_blame import BlameIndex


@dataclass
class _Fn:
    name: str
    start_line: int
    end_line: int


@dataclass
class _Fcx:
    functions: list


class _FileInfo:
    def __init__(self, path: str) -> None:
        self.path = path


class _Pf:
    def __init__(self, path: str) -> None:
        self.file_info = _FileInfo(path)


_NOW = 1_700_000_000
_DAY = 86400


def _walked_and_meta():
    # foo: lines 1-3 (Ann, Ann, Bob); bar: lines 5-6 (Carol, recent).
    idx = BlameIndex(
        lines={
            1: ("s1", _NOW - 400 * _DAY),
            2: ("s1", _NOW - 400 * _DAY),
            3: ("s2", _NOW - 300 * _DAY),
            5: ("s3", _NOW - 10 * _DAY),
            6: ("s3", _NOW - 10 * _DAY),
        },
        authors={
            "s1": ("Ann", "ann@x"),
            "s2": ("Bob", "bob@x"),
            "s3": ("Carol", "carol@x"),
        },
    )
    pf = _Pf("a.py")
    fcx = _Fcx(functions=[_Fn("foo", 1, 3), _Fn("bar", 5, 6), _Fn("dead", 8, 9)])
    return [(pf, fcx)], {"a.py": {"blame_index": idx}}


def test_rollup_emits_only_modified_functions() -> None:
    walked, meta = _walked_and_meta()
    rows = build_function_blame_rows(walked, meta, now_ts=_NOW)
    # "dead" (lines 8-9) has no blame coverage → not emitted.
    assert {r["function_name"] for r in rows} == {"foo", "bar"}


def test_rollup_foo_signals() -> None:
    walked, meta = _walked_and_meta()
    rows = {r["function_name"]: r for r in build_function_blame_rows(walked, meta, now_ts=_NOW)}
    foo = rows["foo"]
    assert foo["symbol_id"] == "a.py::foo"
    assert foo["mod_count"] == 2  # s1, s2
    assert foo["line_count"] == 3
    assert foo["owner_name"] == "Ann"  # 2 of 3 lines
    assert foo["owner_email"] == "ann@x"
    assert abs(foo["owner_line_pct"] - 2 / 3) < 1e-9
    # No commit within the last 90 days → recent_mod_count 0.
    assert foo["recent_mod_count"] == 0
    assert foo["median_author_time"] is not None


def test_rollup_recent_window() -> None:
    walked, meta = _walked_and_meta()
    rows = {r["function_name"]: r for r in build_function_blame_rows(walked, meta, now_ts=_NOW)}
    # bar's only commit is 10 days old → inside the 90-day recent window.
    assert rows["bar"]["recent_mod_count"] == 1
    assert rows["bar"]["mod_count"] == 1


def test_rollup_empty_when_no_blame() -> None:
    pf = _Pf("a.py")
    fcx = _Fcx(functions=[_Fn("foo", 1, 3)])
    # No blame_index in meta → ESSENTIAL-tier no-op.
    assert build_function_blame_rows([(pf, fcx)], {"a.py": {}}, now_ts=_NOW) == []
    # Empty blame index also yields nothing.
    assert (
        build_function_blame_rows(
            [(pf, fcx)], {"a.py": {"blame_index": BlameIndex()}}, now_ts=_NOW
        )
        == []
    )
