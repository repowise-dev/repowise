"""Tests for rolling per-file health signals up to a C4 box.

Pure aggregation, so no database. The rules that matter are the ones that
would misinform a reader: how ownership is picked, and what "unknown" means
as distinct from zero.
"""

from __future__ import annotations

from repowise.server.services.c4_builder.signals import build_box_signals

FILES = {
    "pkg/a.py": "box:one",
    "pkg/b.py": "box:one",
    "pkg/c.py": "box:one",
    "other/d.py": "box:two",
}


def test_every_box_appears_even_with_no_signals() -> None:
    signals = build_box_signals(FILES)
    assert set(signals) == {"box:one", "box:two"}
    assert signals["box:one"].hotspot_count == 0
    assert signals["box:one"].primary_owner is None


def test_hotspots_and_dead_files_are_counted_per_box() -> None:
    signals = build_box_signals(
        FILES, hotspot_paths=["pkg/a.py", "pkg/b.py"], dead_paths=["other/d.py"]
    )
    assert signals["box:one"].hotspot_count == 2
    assert signals["box:one"].dead_count == 0
    assert signals["box:two"].dead_count == 1


def test_a_box_collects_every_layer_its_files_belong_to() -> None:
    """A directory can straddle layers; picking one would be a guess."""
    signals = build_box_signals(
        FILES,
        file_layers={"pkg/a.py": "Domain", "pkg/b.py": "API", "pkg/c.py": "Domain"},
    )
    assert signals["box:one"].layers == ("API", "Domain")
    assert signals["box:two"].layers == ()


def test_the_primary_owner_is_whoever_owns_the_most_files() -> None:
    signals = build_box_signals(
        FILES,
        file_owners={"pkg/a.py": "Ada", "pkg/b.py": "Ada", "pkg/c.py": "Grace"},
    )
    assert signals["box:one"].primary_owner == "Ada"
    assert signals["box:one"].primary_owner_pct == 66.7


def test_an_ownership_tie_breaks_by_name_so_re_exports_do_not_churn() -> None:
    signals = build_box_signals(
        {"a.py": "box", "b.py": "box"},
        file_owners={"a.py": "Zoe", "b.py": "Ada"},
    )
    first = signals["box"].primary_owner
    again = build_box_signals(
        {"b.py": "box", "a.py": "box"},
        file_owners={"b.py": "Ada", "a.py": "Zoe"},
    )["box"].primary_owner
    assert first == again == "Zoe"


def test_the_owner_percentage_counts_every_file_in_the_box() -> None:
    """This used to divide by the attributed files and so always neared 100.

    A reader takes ``repowise.ownerPct`` as "this person owns that much of the
    box". Dividing by the files git resolved an owner for answers a different
    question and reads as a much stronger claim than the data supports.
    """
    signals = build_box_signals(FILES, file_owners={"pkg/a.py": "Ada"})
    assert signals["box:one"].primary_owner == "Ada"
    assert signals["box:one"].primary_owner_pct == 33.3


def test_bus_factor_is_the_worst_case_not_the_average() -> None:
    """One file only one person understands is the risk; a mean hides it."""
    signals = build_box_signals(
        FILES, file_bus_factors={"pkg/a.py": 5, "pkg/b.py": 1, "pkg/c.py": 4}
    )
    assert signals["box:one"].min_bus_factor == 1


def test_missing_signals_stay_none_rather_than_becoming_zero() -> None:
    signals = build_box_signals(FILES)
    box = signals["box:one"]
    assert box.min_bus_factor is None
    assert box.primary_owner_pct is None


def test_a_zero_bus_factor_from_a_file_with_no_history_is_not_a_real_minimum() -> None:
    """The caller filters those out; this pins that 0 is never invented here.

    A bus factor is at least 1 whenever it was computed at all, so a 0
    reaching this function would drag every box's minimum to 0 and read as
    "nobody owns this".
    """
    signals = build_box_signals(FILES, file_bus_factors={"pkg/a.py": 2, "pkg/b.py": 3})
    assert signals["box:one"].min_bus_factor == 2


def test_ownership_percentage_divides_by_the_whole_box() -> None:
    """The denominator is the box, not the files git happened to attribute.

    Dividing by files-with-a-known-owner made one attributed file out of four
    read as "one person owns 100% of this" — a fabricated number, which is
    worse than the missing one the module's own rule asks for.
    """
    signals = build_box_signals(
        {f"pkg/f{i}.py": "pkg:a" for i in range(4)},
        file_owners={"pkg/f0.py": "Ada"},
    )
    assert signals["pkg:a"].primary_owner == "Ada"
    assert signals["pkg:a"].primary_owner_pct == 25.0


def test_ownership_is_omitted_when_almost_nothing_is_attributed() -> None:
    """One owner out of five hundred files is not an ownership signal."""
    signals = build_box_signals(
        {f"pkg/f{i}.py": "pkg:a" for i in range(500)},
        file_owners={"pkg/f0.py": "Ada"},
    )
    assert signals["pkg:a"].primary_owner is None
    assert signals["pkg:a"].primary_owner_pct is None


def test_a_repo_with_no_churn_data_reports_no_hotspot_count() -> None:
    """Zero hotspots and never having looked are different claims.

    An index-only run has no git metadata, so every box would otherwise
    serialize ``repowise.hotspots "0"`` — a clean bill of health nobody
    measured.
    """
    signals = build_box_signals(FILES, churn_measured=False)
    assert signals["box:one"].hotspot_count is None
    # Dead code has no such distinction available, so zero stays zero.
    assert signals["box:one"].dead_count == 0


def test_a_measured_zero_is_still_reported() -> None:
    signals = build_box_signals(FILES, hotspot_paths=())
    assert signals["box:one"].hotspot_count == 0
