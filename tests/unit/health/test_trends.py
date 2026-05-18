"""Tests for ``health/trends.py`` — snapshot diff + alert detection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from repowise.core.analysis.health.trends import (
    DECLINE_LOOKBACK,
    DECLINE_THRESHOLD,
    diff_snapshots,
    recent_kpis,
)


@dataclass
class _S:
    taken_at: datetime
    hotspot_health: float
    average_health: float
    worst_performer_path: str | None = "x"
    worst_performer_score: float | None = 1.0


def _ts(n: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=n)


def _series(values: list[float]) -> list[_S]:
    return [_S(taken_at=_ts(i), hotspot_health=v, average_health=v) for i, v in enumerate(values)]


def test_diff_empty_history_neutral():
    summary = diff_snapshots([])
    assert summary.current_hotspot_health == 10.0
    assert summary.hotspot_delta is None
    assert summary.alerts == []


def test_diff_single_snapshot_no_alerts():
    summary = diff_snapshots(_series([7.5]))
    assert summary.current_hotspot_health == 7.5
    assert summary.previous_hotspot_health is None
    assert summary.alerts == []


def test_declining_health_alert_triggers_at_threshold():
    # 7 snapshots: oldest 8.0, newest 8.0 - threshold - 0.1
    vals = [8.0, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0 - DECLINE_THRESHOLD - 0.1]
    assert len(vals) > DECLINE_LOOKBACK
    summary = diff_snapshots(_series(vals))
    declining = [a for a in summary.alerts if a.kind == "declining"]
    assert declining, "expected a Declining Health alert"
    # Both metrics share the same series → both should fire.
    metrics = {a.metric for a in declining}
    assert "hotspot_health" in metrics
    assert "average_health" in metrics


def test_no_declining_alert_below_threshold():
    vals = [8.0, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0 - 0.1]  # drop too small
    summary = diff_snapshots(_series(vals))
    assert not [a for a in summary.alerts if a.kind == "declining"]


def test_predicted_decline_alert_on_three_consecutive_drops():
    # Four points required: each strictly below the previous.
    summary = diff_snapshots(_series([7.5, 7.4, 7.3, 7.2]))
    pred = [a for a in summary.alerts if a.kind == "predicted_decline"]
    assert pred, "expected a Predicted Decline alert"
    assert all(a.delta < 0 for a in pred)


def test_no_predicted_decline_on_flat_or_recovering():
    summary = diff_snapshots(_series([7.5, 7.4, 7.4, 7.3]))  # plateau
    assert not [a for a in summary.alerts if a.kind == "predicted_decline"]


def test_recent_kpis_orders_newest_first():
    rows = recent_kpis(_series([5.0, 6.0, 7.0]), limit=10)
    scores = [r["hotspot_health"] for r in rows]
    assert scores == [7.0, 6.0, 5.0]
