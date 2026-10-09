"""Per-file trend correctness over normalized readings.

``build_file_points`` / ``file_trend_from_points`` are the storage-free half of
the per-file trend: a reading is ``(taken_at, clamped score, recorded
deduction)`` and nothing about where it was read from survives into them. The
snapshot-shaped path is a thin adapter over these, and is covered by
``test_trends.py``; this pins the contract a second reader has to satisfy.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repowise.core.analysis.health.scoring import SCORE_FLOOR, SCORE_MAX
from repowise.core.analysis.health.trends import (
    MIN_TREND_POINTS,
    build_file_points,
    file_trend_from_points,
)


def _at(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


def _readings(*rows: tuple[float, float | None]) -> list:
    return [(_at(i + 1), score, deduction) for i, (score, deduction) in enumerate(rows)]


# --------------------------------------------------------------------------- #
# Minimum points
# --------------------------------------------------------------------------- #


def test_the_default_minimum_is_two_so_a_lone_reading_draws_nothing() -> None:
    assert MIN_TREND_POINTS == 2
    assert build_file_points(_readings((7.0, None))) == []
    assert len(build_file_points(_readings((7.0, None), (6.0, None)))) == 2


def test_no_readings_is_empty_at_any_minimum() -> None:
    assert build_file_points([], min_points=1) == []


def test_a_consumer_may_lower_the_minimum_to_one() -> None:
    points = build_file_points(_readings((7.0, None)), min_points=1)
    assert [p.score for p in points] == [7.0]


def test_a_single_point_trend_reports_a_current_and_no_movement() -> None:
    trend = file_trend_from_points(
        "a.py", _readings((7.0, None)), snapshot_count=1, min_points=1
    )
    assert trend.current == 7.0
    assert trend.previous is None
    assert trend.delta is None
    assert trend.unclamped_delta is None
    assert trend.declining is False


def test_below_the_minimum_the_trend_is_wholly_neutral() -> None:
    trend = file_trend_from_points("a.py", _readings((7.0, None)), snapshot_count=9)
    assert trend.points == []
    assert (trend.current, trend.previous, trend.delta) == (None, None, None)
    assert trend.declining is False
    # The window size is the caller's, not the number of readings carrying the file.
    assert trend.snapshot_count == 9


# --------------------------------------------------------------------------- #
# Depth-known / all-points rule
# --------------------------------------------------------------------------- #


def test_an_unfloored_series_is_its_own_unclamped_series() -> None:
    points = build_file_points(_readings((8.0, None), (6.5, None)))
    assert [p.unclamped_score for p in points] == [8.0, 6.5]


def test_a_recorded_deduction_undoes_the_floor() -> None:
    points = build_file_points(_readings((SCORE_FLOOR, 12.0), (SCORE_FLOOR, 13.5)))
    assert [p.unclamped_score for p in points] == [
        round(SCORE_MAX - 12.0, 2),
        round(SCORE_MAX - 13.5, 2),
    ]


def test_one_reading_without_depth_flattens_the_whole_series() -> None:
    """Mixing measured depth with unmeasured draws a cliff that never happened."""
    points = build_file_points(_readings((SCORE_FLOOR, None), (SCORE_FLOOR, 13.5)))
    assert [p.unclamped_score for p in points] == [SCORE_FLOOR, SCORE_FLOOR]


def test_depth_appears_only_once_every_reading_carries_it() -> None:
    with_depth = build_file_points(_readings((SCORE_FLOOR, 11.0), (SCORE_FLOOR, 12.0)))
    assert [p.unclamped_score for p in with_depth] == [-1.0, -2.0]


def test_an_above_floor_reading_needs_no_recorded_deduction() -> None:
    """Its score already is the unclamped value, so the series stays known."""
    points = build_file_points(_readings((4.0, None), (SCORE_FLOOR, 12.0)))
    assert [p.unclamped_score for p in points] == [4.0, -2.0]


# --------------------------------------------------------------------------- #
# Decline rules
# --------------------------------------------------------------------------- #


def test_a_single_drop_is_noise_not_a_decline() -> None:
    trend = file_trend_from_points(
        "a.py", _readings((9.0, None), (5.0, None)), snapshot_count=2
    )
    assert trend.delta == -4.0
    assert trend.declining is False


def test_three_consecutive_drops_declare_a_decline() -> None:
    trend = file_trend_from_points(
        "a.py",
        _readings((9.0, None), (8.9, None), (8.8, None), (8.7, None)),
        snapshot_count=4,
    )
    assert trend.declining is True


def test_a_recovering_series_is_not_declining() -> None:
    trend = file_trend_from_points(
        "a.py",
        _readings((9.0, None), (8.0, None), (7.0, None), (8.5, None)),
        snapshot_count=4,
    )
    assert trend.declining is False


def test_a_sustained_drop_over_the_lookback_declares_a_decline() -> None:
    trend = file_trend_from_points(
        "a.py",
        _readings(*[(9.0, None)] * 5, (8.0, None), (8.4, None)),
        snapshot_count=7,
    )
    assert trend.declining is True


def test_a_floored_file_getting_worse_below_the_floor_still_declines() -> None:
    """The clamped line is flat; the recorded depth is what moves."""
    trend = file_trend_from_points(
        "a.py",
        _readings(
            (SCORE_FLOOR, 9.0),
            (SCORE_FLOOR, 10.0),
            (SCORE_FLOOR, 11.0),
            (SCORE_FLOOR, 12.0),
        ),
        snapshot_count=4,
    )
    assert [p.score for p in trend.points] == [SCORE_FLOOR] * 4
    assert trend.delta == 0.0
    assert trend.unclamped_delta == -1.0
    assert trend.declining is True


# --------------------------------------------------------------------------- #
# Summary fields
# --------------------------------------------------------------------------- #


def test_current_previous_and_delta_stay_on_the_clamped_score() -> None:
    trend = file_trend_from_points(
        "a.py", _readings((6.0, None), (4.5, None)), snapshot_count=2
    )
    assert (trend.current, trend.previous, trend.delta) == (4.5, 6.0, -1.5)
    assert trend.unclamped_delta == -1.5


@pytest.mark.parametrize("min_points", [1, 2, 3])
def test_points_are_returned_oldest_first_at_any_minimum(min_points) -> None:
    readings = _readings((9.0, None), (8.0, None), (7.0, None))
    points = build_file_points(readings, min_points=min_points)
    assert [p.taken_at for p in points] == [_at(1), _at(2), _at(3)]
