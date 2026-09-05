"""The hotspot trend is read from the snapshots or left out.

The generated CLAUDE.md printed "(stable)" beside the hotspot KPI as a
literal, whatever the history said (#1490). Now it is the sign of the same
delta the declining alert reads, and nothing at all when there is no history
to read it from.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.analysis.health.trends import (
    DECLINE_LOOKBACK,
    DECLINE_THRESHOLD,
    hotspot_trend,
)


def _history(*values: float) -> list[SimpleNamespace]:
    return [SimpleNamespace(hotspot_health=v, average_health=v) for v in values]


def test_fewer_than_two_snapshots_is_no_trend() -> None:
    assert hotspot_trend([]) is None
    assert hotspot_trend(_history(7.0)) is None


def test_direction_follows_the_delta_against_the_lookback() -> None:
    assert hotspot_trend(_history(7.0, 7.0 - DECLINE_THRESHOLD)) == "declining"
    assert hotspot_trend(_history(7.0, 7.0 + DECLINE_THRESHOLD)) == "improving"
    assert hotspot_trend(_history(7.0, 7.1)) == "stable"


def test_baseline_is_the_lookback_snapshot_not_the_previous_one() -> None:
    """Five small drops read as declining; the last step alone would not."""
    step = DECLINE_THRESHOLD / DECLINE_LOOKBACK
    values = [8.0 - i * step for i in range(DECLINE_LOOKBACK + 1)]
    assert hotspot_trend(_history(*values)) == "declining"
    assert hotspot_trend(_history(*values[-2:])) == "stable"
