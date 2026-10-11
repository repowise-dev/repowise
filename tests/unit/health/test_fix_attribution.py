"""Tests for ``health/fix_attribution.py`` — symbols per fix, and decayed mass.

The two claims this module makes to the surfaces are that a fix lands in the
narrowest symbol it touched, and that a file's "bug magnet" flag is about recent
fixes rather than a lifetime tally. Both are asserted here; both are the reason
the surfaces are allowed to say "mostly this function" and "last fixed N weeks
ago" at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from repowise.core.analysis.health.fix_attribution import (
    BUG_MAGNET_MASS,
    FIX_HALF_LIFE_DAYS,
    SymbolSpan,
    attribute_ranges,
    attribution_kind,
    decayed_mass,
    roll_up_file,
)

NOW = datetime(2026, 7, 1, tzinfo=UTC)


def _spans() -> list[SymbolSpan]:
    return [
        SymbolSpan("m.py::Store", 1, 60),
        SymbolSpan("m.py::Store.save", 10, 25),
        SymbolSpan("m.py::Store.load", 30, 45),
        SymbolSpan("m.py::helper", 70, 80),
    ]


def test_range_attributes_to_the_narrowest_containing_symbol():
    # Lines 12-14 sit inside both the class and one of its methods.
    assert attribute_ranges([(12, 14)], _spans()) == ["m.py::Store.save"]


def test_range_spanning_two_methods_attributes_to_both_not_the_class():
    assert attribute_ranges([(20, 35)], _spans()) == [
        "m.py::Store.save",
        "m.py::Store.load",
    ]


def test_range_in_class_body_outside_any_method_keeps_the_class():
    assert attribute_ranges([(2, 5)], _spans()) == ["m.py::Store"]


def test_disjoint_ranges_union_their_symbols_in_file_order():
    assert attribute_ranges([(75, 76), (11, 12)], _spans()) == [
        "m.py::Store.save",
        "m.py::helper",
    ]


def test_pure_insertion_and_unparsed_file_attribute_to_nothing():
    assert attribute_ranges([], _spans()) == []
    assert attribute_ranges([(1, 5)], []) == []


def test_range_between_symbols_attributes_to_nothing():
    assert attribute_ranges([(62, 66)], _spans()) == []


def test_degenerate_symbol_spans_are_skipped():
    bad = [SymbolSpan("m.py::a", 0, 0), SymbolSpan("m.py::b", 9, 3)]
    assert attribute_ranges([(1, 100)], bad) == []


def test_attribution_is_exact_only_when_nothing_touched_the_file_since():
    fixed = datetime(2026, 6, 1, tzinfo=UTC)
    assert attribution_kind(["s"], fixed, fixed) == "exact"
    assert attribution_kind(["s"], fixed, fixed + timedelta(days=1)) == "approximate"
    # No symbols is "none" regardless of how fresh the row is.
    assert attribution_kind([], fixed, fixed) == "none"
    # An unreadable timestamp cannot prove exactness, so it does not claim it.
    assert attribution_kind(["s"], None, fixed) == "approximate"
    assert attribution_kind(["s"], fixed, None) == "approximate"


def test_decay_halves_over_one_half_life():
    assert decayed_mass(NOW, NOW) == 1.0
    older = NOW - timedelta(days=FIX_HALF_LIFE_DAYS)
    assert round(decayed_mass(older, NOW), 6) == 0.5
    assert round(decayed_mass(NOW - timedelta(days=2 * FIX_HALF_LIFE_DAYS), NOW), 6) == 0.25
    # Committer clock skew must not mint mass above a fresh fix.
    assert decayed_mass(NOW + timedelta(days=3), NOW) == 1.0
    assert decayed_mass(None, NOW) == 0.0


def test_three_fresh_fixes_flag_a_magnet_and_three_stale_ones_do_not():
    fresh = [(NOW, ["m.py::f"]) for _ in range(3)]
    assert roll_up_file(fresh, NOW).bug_magnet is True

    stale_at = NOW - timedelta(days=FIX_HALF_LIFE_DAYS)
    stale = [(stale_at, ["m.py::f"]) for _ in range(3)]
    rollup = roll_up_file(stale, NOW)
    assert rollup.bug_magnet is False
    assert rollup.fix_count == 3  # the count is undecayed; only the mass ages
    assert round(rollup.fix_mass, 2) == 1.5


def test_rollup_reports_last_fix_and_symbol_counts_by_descending_count():
    events = [
        (NOW - timedelta(days=40), ["m.py::a"]),
        (NOW - timedelta(days=5), ["m.py::a", "m.py::b"]),
        (NOW - timedelta(days=90), ["m.py::b"]),
    ]
    rollup = roll_up_file(events, NOW)
    assert rollup.last_fix_at == NOW - timedelta(days=5)
    assert list(rollup.symbol_counts.items()) == [("m.py::a", 2), ("m.py::b", 2)]
    assert rollup.fix_mass < BUG_MAGNET_MASS


def test_empty_history_rolls_up_to_a_clean_zero():
    rollup = roll_up_file([], NOW)
    assert rollup == type(rollup)(
        fix_count=0, fix_mass=0.0, bug_magnet=False, last_fix_at=None, symbol_counts={}
    )
