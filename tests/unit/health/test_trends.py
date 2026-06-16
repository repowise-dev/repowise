"""Tests for ``health/trends.py`` — snapshot diff + alert detection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from repowise.core.analysis.health.trends import (
    DECLINE_LOOKBACK,
    DECLINE_THRESHOLD,
    diff_snapshots,
    file_score_series,
    file_trend,
    recent_kpis,
)


@dataclass
class _S:
    taken_at: datetime
    hotspot_health: float
    average_health: float
    worst_performer_path: str | None = "x"
    worst_performer_score: float | None = 1.0
    per_file_scores_json: str = "{}"


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


# --------------------------------------------------------------------------- #
# Per-file trajectory
# --------------------------------------------------------------------------- #


def _file_series(per_file: list[dict[str, float]]) -> list[_S]:
    """Build snapshots whose only varying field is the per-file score map."""
    return [
        _S(
            taken_at=_ts(i),
            hotspot_health=10.0,
            average_health=10.0,
            per_file_scores_json=json.dumps(scores),
        )
        for i, scores in enumerate(per_file)
    ]


def test_file_score_series_extracts_oldest_first():
    snaps = _file_series([{"a.py": 9.0}, {"a.py": 8.0}, {"a.py": 7.0}])
    pts = file_score_series(snaps, "a.py")
    assert [p.score for p in pts] == [9.0, 8.0, 7.0]
    # Ordering is preserved oldest -> newest.
    assert pts[0].taken_at == _ts(0)
    assert pts[-1].taken_at == _ts(2)


def test_file_score_series_skips_snapshots_missing_the_file():
    # The file is absent from the middle snapshot — the gap is skipped, not
    # zero-filled, so the line connects the two real points.
    snaps = _file_series([{"a.py": 9.0}, {"b.py": 5.0}, {"a.py": 7.0}])
    pts = file_score_series(snaps, "a.py")
    assert [p.score for p in pts] == [9.0, 7.0]


def test_file_score_series_silent_below_two_points():
    # One point is not a trend.
    snaps = _file_series([{"a.py": 9.0}, {"b.py": 5.0}])
    assert file_score_series(snaps, "a.py") == []
    # Zero points likewise.
    assert file_score_series(snaps, "missing.py") == []


def test_file_score_series_tolerates_bad_json():
    snaps = _file_series([{"a.py": 9.0}, {"a.py": 8.0}])
    snaps.insert(1, _S(_ts(9), 10.0, 10.0, per_file_scores_json="not json"))
    pts = file_score_series(snaps, "a.py")
    assert [p.score for p in pts] == [9.0, 8.0]


def test_file_trend_summary_delta():
    snaps = _file_series([{"a.py": 8.0}, {"a.py": 6.5}])
    t = file_trend(snaps, "a.py")
    assert t.current == 6.5
    assert t.previous == 8.0
    assert t.delta == -1.5
    assert t.snapshot_count == 2


def test_file_trend_thin_history_is_neutral():
    snaps = _file_series([{"a.py": 8.0}])
    t = file_trend(snaps, "a.py")
    assert t.points == []
    assert t.current is None
    assert t.previous is None
    assert t.delta is None
    assert t.declining is False
    assert t.snapshot_count == 1


def test_file_trend_declining_on_sustained_drop():
    # > DECLINE_LOOKBACK points, newest >= threshold below the lookback point.
    vals = [{"a.py": 8.0} for _ in range(DECLINE_LOOKBACK)]
    vals.append({"a.py": 8.0 - DECLINE_THRESHOLD - 0.1})
    t = file_trend(_file_series(vals), "a.py")
    assert t.declining is True


def test_file_trend_declining_on_consecutive_drops():
    t = file_trend(
        _file_series([{"a.py": 9.0}, {"a.py": 8.8}, {"a.py": 8.6}, {"a.py": 8.4}]), "a.py"
    )
    assert t.declining is True


def test_file_trend_not_declining_when_recovering():
    t = file_trend(_file_series([{"a.py": 7.0}, {"a.py": 8.0}, {"a.py": 9.0}]), "a.py")
    assert t.declining is False
