"""Line to enclosing symbol over stored spans."""

from __future__ import annotations

from dataclasses import dataclass

from repowise.core.symbol_spans import symbol_at, symbols_overlapping


@dataclass(frozen=True)
class _S:
    name: str
    start_line: int
    end_line: int


CLASS = _S("Class", 1, 30)
METHOD = _S("Class.method", 5, 12)
NESTED = _S("Class.method.inner", 7, 9)
OTHER = _S("Class.other", 14, 20)
SPANS = [CLASS, METHOD, NESTED, OTHER]


def test_symbol_at_picks_the_innermost_span() -> None:
    assert symbol_at(SPANS, 8) is NESTED
    assert symbol_at(SPANS, 5) is METHOD
    assert symbol_at(SPANS, 13) is CLASS
    assert symbol_at(SPANS, 15) is OTHER


def test_symbol_at_ends_are_inclusive() -> None:
    assert symbol_at(SPANS, 9) is NESTED
    assert symbol_at(SPANS, 12) is METHOD
    assert symbol_at(SPANS, 30) is CLASS


def test_symbol_at_outside_every_span_is_none() -> None:
    assert symbol_at(SPANS, 31) is None
    assert symbol_at([], 1) is None


def test_symbol_at_ignores_input_order() -> None:
    assert symbol_at(list(reversed(SPANS)), 8) is NESTED


def test_symbol_at_same_start_goes_to_the_shorter_span() -> None:
    wide, narrow = _S("wide", 3, 10), _S("narrow", 3, 4)
    assert symbol_at([wide, narrow], 3) is narrow
    assert symbol_at([narrow, wide], 3) is narrow


def test_symbol_at_identical_spans_go_to_the_last() -> None:
    first, second = _S("first", 2, 2), _S("second", 2, 2)
    assert symbol_at([first, second], 2) is second


def test_symbols_overlapping_keeps_input_order() -> None:
    assert symbols_overlapping(SPANS, [(8, 8)]) == [CLASS, METHOD, NESTED]
    assert symbols_overlapping(SPANS, [(13, 13), (20, 40)]) == [CLASS, OTHER]


def test_symbols_overlapping_touching_bounds_count() -> None:
    assert symbols_overlapping([METHOD], [(1, 5)]) == [METHOD]
    assert symbols_overlapping([METHOD], [(12, 99)]) == [METHOD]
    assert symbols_overlapping([METHOD], [(13, 99)]) == []


def test_symbols_overlapping_accepts_list_ranges() -> None:
    assert symbols_overlapping([METHOD], [[6, 6]]) == [METHOD]


def test_symbols_overlapping_skips_spans_without_lines() -> None:
    unplaced, backwards = _S("unplaced", 0, 50), _S("backwards", 9, 3)
    assert symbols_overlapping([unplaced, backwards, METHOD], [(1, 100)]) == [METHOD]


def test_symbols_overlapping_no_ranges_is_empty() -> None:
    assert symbols_overlapping(SPANS, []) == []
