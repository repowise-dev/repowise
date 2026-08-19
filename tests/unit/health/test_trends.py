"""Tests for ``health/trends.py`` — snapshot diff + alert detection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from repowise.core.analysis.health.scoring import SCORE_FLOOR
from repowise.core.analysis.health.trends import (
    DECLINE_LOOKBACK,
    DECLINE_THRESHOLD,
    diff_snapshots,
    file_score_series,
    file_trend,
    recent_kpis,
    snapshot_file_maps,
)


@dataclass
class _S:
    taken_at: datetime
    hotspot_health: float
    average_health: float
    worst_performer_path: str | None = "x"
    worst_performer_score: float | None = 1.0
    per_file_scores_json: str = "{}"
    # Deliberately defaulted to the empty map rather than omitted: a snapshot
    # written before deductions were captured has exactly this, and the
    # "history stays flat" tests below depend on that being the shape.
    per_file_deductions_json: str = "{}"


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


# --------------------------------------------------------------------------- #
# Below the floor
#
# The stored score clamps at SCORE_FLOOR, so a file deep enough to sit on it
# has a flat series however much of the work gets done. Snapshots record the
# pre-clamp deduction for those files only; ``unclamped_score`` is the series
# that can still move.
# --------------------------------------------------------------------------- #


@dataclass
class _Metric:
    file_path: str
    score: float


@dataclass
class _Finding:
    file_path: str
    health_impact: float


def _floored_series(
    per_file: list[dict[str, float]],
    per_file_ded: list[dict[str, float]],
) -> list[_S]:
    """Snapshots carrying both maps, zipped positionally."""
    return [
        _S(
            taken_at=_ts(i),
            hotspot_health=10.0,
            average_health=10.0,
            per_file_scores_json=json.dumps(scores),
            per_file_deductions_json=json.dumps(ded),
        )
        for i, (scores, ded) in enumerate(zip(per_file, per_file_ded, strict=True))
    ]


def test_unclamped_score_tracks_the_recorded_deduction():
    # The motivating case: three snapshots, all printing 1.0, while 3.3 points
    # of deduction were actually cleared. The visible series says nothing
    # happened; the unclamped one says a third of the way there.
    snaps = _floored_series(
        [{"a.py": 1.0}, {"a.py": 1.0}, {"a.py": 1.0}],
        [{"a.py": 12.9}, {"a.py": 11.2}, {"a.py": 9.6}],
    )
    pts = file_score_series(snaps, "a.py")
    assert [p.score for p in pts] == [1.0, 1.0, 1.0]
    assert [p.unclamped_score for p in pts] == [-2.9, -1.2, 0.4]

    t = file_trend(snaps, "a.py")
    # The surfaced score is unchanged — it really is 1.0 and it really has not
    # moved. The unclamped delta is where the work shows up.
    assert t.current == 1.0
    assert t.delta == 0.0
    assert t.unclamped_delta == 1.6


def test_unclamped_score_equals_score_without_a_recorded_deduction():
    # Every file the floor never touches, and every row written before
    # deductions were captured: one series, no divergence, no second code path.
    snaps = _file_series([{"a.py": 9.0}, {"a.py": 7.5}])
    pts = file_score_series(snaps, "a.py")
    assert [p.unclamped_score for p in pts] == [9.0, 7.5]
    t = file_trend(snaps, "a.py")
    assert t.unclamped_delta == t.delta == -1.5


def test_pre_existing_floored_history_stays_flat():
    # A repo indexed before this landed has scores but no deductions. Its
    # floored files must stay flat rather than acquire an invented depth.
    snaps = _file_series([{"a.py": 1.0}, {"a.py": 1.0}, {"a.py": 1.0}])
    t = file_trend(snaps, "a.py")
    assert [p.unclamped_score for p in t.points] == [1.0, 1.0, 1.0]
    assert t.unclamped_delta == 0.0
    assert t.declining is False


def test_a_partial_deduction_history_reports_no_depth_at_all():
    """The shape the first index after this ships produces, and the one that
    has to stay quiet.

    Old rows carry no deduction; the newest does. Reading the old points as
    1.0 and the new one as its real depth draws a cliff on a file that did not
    change — measured against the live index, that flipped 21 of 32 floored
    files to ``declining`` on an index where nothing moved. The series
    therefore stays clamped until the whole window has depth.
    """
    snaps = _floored_series(
        [{"a.py": 1.0}, {"a.py": 1.0}, {"a.py": 1.0}],
        [{}, {}, {"a.py": 11.0}],
    )
    pts = file_score_series(snaps, "a.py")
    assert [p.unclamped_score for p in pts] == [1.0, 1.0, 1.0]

    t = file_trend(snaps, "a.py")
    assert t.unclamped_delta == 0.0
    assert t.declining is False


def test_depth_appears_once_the_whole_window_has_it():
    """The other side of the gate: nothing is lost, only deferred."""
    snaps = _floored_series(
        [{"a.py": 1.0}, {"a.py": 1.0}, {"a.py": 1.0}],
        [{"a.py": 12.0}, {"a.py": 11.5}, {"a.py": 11.0}],
    )
    assert [p.unclamped_score for p in file_score_series(snaps, "a.py")] == [-2.0, -1.5, -1.0]
    assert file_trend(snaps, "a.py").unclamped_delta == 0.5


def test_an_unfloored_point_counts_as_known_depth():
    """A file that collapsed *into* the floor must still chart the collapse.

    Its early points are above the floor, where the score is already the
    unclamped value, so the window is complete even though only the tail
    carries a recorded deduction. Gating on "every point has a deduction"
    instead of "every point has a known depth" would have silenced this.
    """
    snaps = _floored_series(
        [{"a.py": 8.8}, {"a.py": 5.2}, {"a.py": 3.5}, {"a.py": 1.0}],
        [{}, {}, {}, {"a.py": 12.0}],
    )
    assert [p.unclamped_score for p in file_score_series(snaps, "a.py")] == [8.8, 5.2, 3.5, -2.0]
    t = file_trend(snaps, "a.py")
    assert t.unclamped_delta == -5.5
    # Four points, each below the last, so the consecutive-drop rule fires.
    assert t.declining is True


def test_unclamped_score_ignores_a_deduction_for_a_different_file():
    # The deduction map is keyed by path like the score map; a hit on a
    # neighbour must not leak into this file's depth.
    snaps = _floored_series(
        [{"a.py": 1.0}, {"a.py": 1.0}],
        [{"b.py": 12.0}, {"b.py": 11.0}],
    )
    assert [p.unclamped_score for p in file_score_series(snaps, "a.py")] == [1.0, 1.0]


def test_unclamped_series_skips_snapshots_missing_the_file():
    # A deduction recorded in a snapshot that does not score the file at all
    # cannot manufacture a point — the score map is what decides membership.
    snaps = _floored_series(
        [{"a.py": 1.0}, {"b.py": 5.0}, {"a.py": 1.0}],
        [{"a.py": 12.0}, {"a.py": 3.0}, {"a.py": 10.5}],
    )
    pts = file_score_series(snaps, "a.py")
    assert len(pts) == 2
    assert [p.unclamped_score for p in pts] == [-2.0, -0.5]


def test_unclamped_score_tolerates_a_broken_deduction_map():
    # A deduction map that will not parse is an unknown depth, not a zero one,
    # so the series stays clamped rather than reporting a 3-point drop into
    # the unparseable snapshot. The score map still drives the points.
    snaps = _floored_series([{"a.py": 1.0}, {"a.py": 1.0}], [{"a.py": 12.0}, {}])
    snaps[1].per_file_deductions_json = "not json"
    pts = file_score_series(snaps, "a.py")
    assert [p.score for p in pts] == [1.0, 1.0]
    assert [p.unclamped_score for p in pts] == [1.0, 1.0]


def test_declining_fires_for_a_file_getting_worse_below_the_floor():
    # The flag's whole claim is "is it getting worse now". On the clamped
    # series it could never answer yes for a floored file; every value is 1.0.
    worsening = _floored_series(
        [{"a.py": 1.0}] * 4,
        [{"a.py": 9.5}, {"a.py": 10.5}, {"a.py": 11.5}, {"a.py": 12.5}],
    )
    assert file_trend(worsening, "a.py").declining is True
    # And the same shape without recorded depth cannot, which is what makes
    # the assertion above about this change rather than about the fixture.
    assert file_trend(_file_series([{"a.py": 1.0}] * 4), "a.py").declining is False


def test_declining_stays_false_while_a_floored_file_recovers():
    recovering = _floored_series(
        [{"a.py": 1.0}] * 4,
        [{"a.py": 12.5}, {"a.py": 11.5}, {"a.py": 10.5}, {"a.py": 9.5}],
    )
    assert file_trend(recovering, "a.py").declining is False


# --------------------------------------------------------------------------- #
# snapshot_file_maps — the writer half
# --------------------------------------------------------------------------- #


def test_snapshot_file_maps_records_depth_only_at_the_floor():
    metrics = [_Metric("floored.py", SCORE_FLOOR), _Metric("fine.py", 7.5)]
    findings = [
        _Finding("floored.py", 6.0),
        _Finding("floored.py", 6.9),
        _Finding("fine.py", 2.5),
    ]
    scores, deductions = snapshot_file_maps(metrics, findings)
    assert scores == {"floored.py": 1.0, "fine.py": 7.5}
    # Summed across the file's findings, and only for the floored one — the
    # unfloored file's deduction is exactly 10 - 7.5 and needs no storage.
    assert deductions == {"floored.py": 12.9}


def test_snapshot_file_maps_omits_a_floored_file_with_no_findings():
    # Nothing to sum means nothing is known; recording 0.0 would claim the
    # file is at 10.0 and invert its trend.
    scores, deductions = snapshot_file_maps([_Metric("a.py", SCORE_FLOOR)], [])
    assert scores == {"a.py": 1.0}
    assert deductions == {}


def test_snapshot_file_maps_ignores_findings_for_unscored_files():
    # Findings can outlive a metric row (a file dropped from this run). The
    # deduction map is keyed off metrics so it cannot grow phantom entries.
    scores, deductions = snapshot_file_maps(
        [_Metric("a.py", SCORE_FLOOR)],
        [_Finding("a.py", 11.0), _Finding("gone.py", 9.0)],
    )
    assert scores == {"a.py": 1.0}
    assert deductions == {"a.py": 11.0}


def test_snapshot_file_maps_round_trips_into_the_reader():
    # The two halves are written and read by different modules; this pins that
    # they agree on the same file's depth end to end.
    metrics = [_Metric("a.py", SCORE_FLOOR)]
    before = snapshot_file_maps(metrics, [_Finding("a.py", 12.0)])
    after = snapshot_file_maps(metrics, [_Finding("a.py", 10.0)])
    snaps = _floored_series([before[0], after[0]], [before[1], after[1]])
    t = file_trend(snaps, "a.py")
    assert t.delta == 0.0
    assert t.unclamped_delta == 2.0


# --------------------------------------------------------------------------- #
# The per-snapshot parse memo
# --------------------------------------------------------------------------- #
#
# The per-file maps are per-*repo* blobs read one file at a time, so asking
# about N files re-parsed the same few blobs N times. Measured on the repowise
# index (20 snapshots averaging 186 KB each), ``get_health`` on a ``module:``
# target expanding to 822 files spent 13.0s in ``json.loads``; memoized, 0.4s.


def _counting_loads(monkeypatch) -> list[int]:
    """Count ``json.loads`` calls made by ``trends``, without changing them.

    Clears the memo first: it is content-keyed and process-wide, so a blob an
    earlier test already parsed would otherwise make this one pass for the
    wrong reason.
    """
    import repowise.core.analysis.health.trends as trends_mod

    trends_mod._parsed_map.cache_clear()
    calls = [0]
    real = json.loads

    def counted(s, *a, **kw):
        calls[0] += 1
        return real(s, *a, **kw)

    monkeypatch.setattr(trends_mod.json, "loads", counted)
    return calls


def test_each_snapshot_map_is_parsed_once_however_many_files_are_asked_about(monkeypatch):
    paths = [f"f{i}.py" for i in range(40)]
    snaps = _file_series([{p: 9.0 for p in paths}, {p: 8.0 for p in paths}])
    calls = _counting_loads(monkeypatch)

    for p in paths:
        assert file_trend(snaps, p).delta == -1.0

    # One parse per distinct blob: two score maps, and the one empty deduction
    # map both snapshots share. Unmemoized this is 40x that, and it is the shape
    # that made a ``module:`` target take 13 seconds.
    assert calls[0] <= 3, f"re-parsed per file: {calls[0]} loads"


def test_the_memo_is_keyed_on_content_so_it_cannot_serve_a_stale_map():
    """Different bytes are a different key.

    A memo keyed on ``(snapshot, column)`` would keep answering with the first
    map it ever saw. These are historical rows so a rewrite is not expected —
    but a cache that *cannot* notice one is a different promise from a cache
    that re-parses when the bytes change.
    """
    snaps = _file_series([{"a.py": 9.0}, {"a.py": 8.0}])
    assert file_trend(snaps, "a.py").current == 8.0

    snaps[-1].per_file_scores_json = json.dumps({"a.py": 3.0})
    assert file_trend(snaps, "a.py").current == 3.0


def test_the_memo_needs_nothing_of_the_snapshot_object():
    """Content-keying is what lets an unhashable row through.

    The obvious memo — a ``WeakKeyDictionary`` on the snapshot — silently
    degrades to no caching for any snapshot type that is unhashable or not
    weak-referenceable, which is every ``@dataclass`` row (``eq=True`` sets
    ``__hash__`` to ``None``) and every ``__slots__`` stub.
    """

    class _Slotted:
        __slots__ = ("per_file_deductions_json", "per_file_scores_json", "taken_at")

        def __init__(self, taken_at, scores):
            self.taken_at = taken_at
            self.per_file_scores_json = json.dumps(scores)
            self.per_file_deductions_json = "{}"

    snaps = [_Slotted(_ts(0), {"a.py": 9.0}), _Slotted(_ts(1), {"a.py": 7.0})]
    assert file_trend(snaps, "a.py").delta == -2.0
