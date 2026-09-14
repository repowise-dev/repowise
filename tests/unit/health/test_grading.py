"""Tests for the health band + distribution "currency" layer.

The cutoffs are absolute and mirrored in ``packages/types/src/health.ts``; the
boundary assertions here are the Python half of the cross-language parity
guard.
"""

from __future__ import annotations

from repowise.core.analysis.health.grading import (
    BAND_BADGE_COLOR,
    BAND_LABEL,
    BAND_ORDER,
    BAND_RANGE_LABEL,
    BAND_TERMINAL_COLOR,
    EXCELLENT_MIN,
    FAIR_MIN,
    GOOD_MIN,
    NEEDS_WORK_MIN,
    TARGET_SCORE,
    band_for,
    distribution,
)


def test_cutoffs_are_frozen() -> None:
    # If these change, the TS mirror (band-cutoffs test) must change too.
    assert EXCELLENT_MIN == 8.5
    assert GOOD_MIN == 7.0
    assert FAIR_MIN == 5.5
    assert NEEDS_WORK_MIN == 4.0


def test_the_refactoring_target_is_not_a_band_edge() -> None:
    # Bands are presentation; this is the number refactoring leverage is
    # measured against. Moving it would reorder every recommendation, so it
    # stays where it was when the bands moved around it.
    assert TARGET_SCORE == 8.0
    assert TARGET_SCORE not in {EXCELLENT_MIN, GOOD_MIN, FAIR_MIN, NEEDS_WORK_MIN}


def test_band_for_boundaries() -> None:
    assert band_for(10.0) == "excellent"
    assert band_for(8.5) == "excellent"
    assert band_for(8.49) == "good"
    assert band_for(7.0) == "good"
    assert band_for(6.99) == "fair"
    assert band_for(5.5) == "fair"
    assert band_for(5.49) == "needs_work"
    assert band_for(4.0) == "needs_work"
    assert band_for(3.99) == "at_risk"
    assert band_for(1.0) == "at_risk"


def test_every_band_has_a_label_a_range_and_a_colour() -> None:
    bands = set(BAND_ORDER)
    assert bands == {"at_risk", "needs_work", "fair", "good", "excellent"}
    for table in (BAND_LABEL, BAND_RANGE_LABEL, BAND_TERMINAL_COLOR, BAND_BADGE_COLOR):
        assert set(table) == bands


def test_range_labels_state_the_real_cutoffs() -> None:
    # These strings are what legends and keys print. Pinned to the constants
    # they describe, so moving a cutoff cannot leave a legend telling the user
    # a boundary that is no longer there.
    expected = {
        "excellent": f"{EXCELLENT_MIN}+",
        "good": f"{GOOD_MIN} to {EXCELLENT_MIN}",
        "fair": f"{FAIR_MIN} to {GOOD_MIN}",
        "needs_work": f"{NEEDS_WORK_MIN} to {FAIR_MIN}",
        "at_risk": f"under {NEEDS_WORK_MIN}",
    }
    assert dict(BAND_RANGE_LABEL) == expected


def test_terminal_and_badge_colours_are_named_and_ordered() -> None:
    # A typo reaches a user as a rich markup error or a grey shields badge, and
    # neither surface is covered by a rendering test.
    assert dict(BAND_TERMINAL_COLOR) == {
        "excellent": "green",
        "good": "green",
        "fair": "yellow",
        "needs_work": "dark_orange",
        "at_risk": "red",
    }
    assert dict(BAND_BADGE_COLOR) == {
        "excellent": "brightgreen",
        "good": "brightgreen",
        "fair": "yellow",
        "needs_work": "orange",
        "at_risk": "red",
    }


def test_band_order_runs_worst_first() -> None:
    assert BAND_ORDER == ("at_risk", "needs_work", "fair", "good", "excellent")


def test_excellent_and_good_share_one_green() -> None:
    # They are told apart by the word, not by the colour.
    assert BAND_TERMINAL_COLOR["excellent"] == BAND_TERMINAL_COLOR["good"]
    assert BAND_BADGE_COLOR["excellent"] == BAND_BADGE_COLOR["good"]


def test_distribution_empty_repo() -> None:
    dist = distribution([])
    assert dist["total_files"] == 0
    assert dist["total_nloc"] == 0
    for band in BAND_ORDER:
        assert dist["bands"][band] == {"files": 0, "nloc": 0, "pct": 0.0}


def test_distribution_nloc_weighted() -> None:
    # One excellent file with lots of NLOC vs many tiny at-risk files: the
    # percentage is NLOC-weighted, not file-count-weighted.
    metrics = [
        {"file_path": "big_healthy.py", "score": 9.0, "nloc": 900},
        {"file_path": "a.py", "score": 2.0, "nloc": 50},
        {"file_path": "b.py", "score": 3.0, "nloc": 50},
    ]
    dist = distribution(metrics)
    assert dist["total_files"] == 3
    assert dist["total_nloc"] == 1000
    assert dist["bands"]["excellent"] == {"files": 1, "nloc": 900, "pct": 90.0}
    assert dist["bands"]["at_risk"] == {"files": 2, "nloc": 100, "pct": 10.0}
    assert dist["bands"]["fair"] == {"files": 0, "nloc": 0, "pct": 0.0}


def test_distribution_floors_zero_nloc_at_one() -> None:
    # A zero-NLOC file still counts once toward its band's weight.
    metrics = [
        {"file_path": "z.py", "score": 9.0, "nloc": 0},
        {"file_path": "w.py", "score": 5.0, "nloc": 0},
    ]
    dist = distribution(metrics)
    assert dist["total_nloc"] == 2
    assert dist["bands"]["excellent"]["nloc"] == 1
    assert dist["bands"]["needs_work"]["nloc"] == 1


def test_distribution_accepts_objects() -> None:
    class _M:
        def __init__(self, path: str, score: float, nloc: int) -> None:
            self.file_path = path
            self.score = score
            self.nloc = nloc

    dist = distribution([_M("a.py", 9.0, 100), _M("b.py", 2.0, 100)])
    assert dist["bands"]["excellent"]["pct"] == 50.0
    assert dist["bands"]["at_risk"]["pct"] == 50.0
