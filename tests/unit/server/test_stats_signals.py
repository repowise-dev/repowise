"""Unit tests for the Stats page's derived signals.

Covers the punch-card fold, commit velocity, the truck-factor rollup, and the
single ``build_commit_pass`` scan that produces the origin / rhythm / people sections
from one read of the commits table.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from repowise.core.stats_highlights import (
    _chronotypes,
    _commit_velocity,
    _punch_card_summary,
    build_commit_pass,
    build_people,
)


def _commit(sha: str, dt: datetime | str) -> dict:
    return {
        "sha": sha,
        "author_name": "Jane Doe",
        "author_email": "jane@company.com",
        "committed_at": dt,
        "subject": f"commit {sha}",
        "lines_added": 5,
        "lines_deleted": 1,
        "files_changed": 1,
    }


# ---------------------------------------------------------------------------
# Pure summarizers
# ---------------------------------------------------------------------------


def test_punch_card_summary_peak() -> None:
    punch = [[0] * 24 for _ in range(7)]
    punch[2][14] = 10  # Wednesday 2pm — the peak
    punch[0][9] = 4  # Monday 9am
    punch[5][11] = 3  # Saturday (weekend)
    punch[6][12] = 2  # Sunday (weekend)
    dated = 10 + 4 + 3 + 2

    out = _punch_card_summary(punch, dated_total=dated, timezone_mode="utc")

    assert out["peak"] == {"weekday": 2, "hour": 14, "count": 10}
    assert out["busiest_weekday"] == 2
    assert out["peak_hour"] == 14
    assert out["total"] == dated


def test_punch_card_summary_empty() -> None:
    out = _punch_card_summary([[0] * 24 for _ in range(7)], dated_total=0, timezone_mode="utc")
    assert out["peak"] is None
    assert out["busiest_weekday"] is None
    assert out["total"] == 0


def test_chronotypes_ship_both_marginals() -> None:
    """The UI names people from these arrays, so they must survive the trip.

    Weekday counts in particular cannot be derived client-side from anything
    else in the payload, and without them a contributor can never be read as
    working weekends.
    """
    hours = [0] * 24
    hours[23] = 30
    weekdays = [0] * 7
    weekdays[5] = 20
    weekdays[2] = 10

    out = _chronotypes({"a@b.com": hours}, {"a@b.com": weekdays}, {"a@b.com": "Someone"})

    assert len(out) == 1
    assert out[0]["hour_commits"] == hours
    assert out[0]["weekday_commits"] == weekdays
    assert out[0]["commits"] == 30
    assert out[0]["label"] == "night_owl"


def test_chronotypes_default_weekdays_when_missing() -> None:
    """A person present in one map but not the other must not blow up the page."""
    hours = [0] * 24
    hours[14] = 40

    out = _chronotypes({"a@b.com": hours}, {}, {})

    assert out[0]["weekday_commits"] == [0] * 7


def test_commit_velocity_rising_and_no_prior() -> None:
    anchor = datetime(2026, 6, 1, tzinfo=UTC)
    recent = [anchor - timedelta(days=d) for d in (1, 10, 40, 80)]  # 4 in last 90d
    prior = [anchor - timedelta(days=d) for d in (100, 150)]  # 2 in prior 90d
    out = _commit_velocity(recent + prior, anchor, None)
    assert out["recent_90d"] == 4
    assert out["prior_90d"] == 2
    assert out["pct_change"] == 100.0  # 4 vs 2

    # No prior window → pct_change is None rather than a divide-by-zero spike.
    young = _commit_velocity(recent, anchor, None)
    assert young["prior_90d"] == 0
    assert young["pct_change"] is None


def test_commit_velocity_empty() -> None:
    assert _commit_velocity([], None, None) is None


def test_commit_velocity_withheld_when_the_sample_is_shorter_than_the_window() -> None:
    """A sample that starts inside the 90-day window would undercount it."""
    anchor = datetime(2026, 6, 1, tzinfo=UTC)
    times = [anchor - timedelta(days=d) for d in (1, 10, 40)]
    assert _commit_velocity(times, anchor, anchor - timedelta(days=40)) is None


def test_commit_velocity_drops_the_comparison_when_the_prior_window_is_cut() -> None:
    anchor = datetime(2026, 6, 1, tzinfo=UTC)
    times = [anchor - timedelta(days=d) for d in (1, 10, 100, 120)]
    out = _commit_velocity(times, anchor, anchor - timedelta(days=120))
    assert out is not None
    assert out["recent_90d"] == 2
    assert out["pct_change"] is None


# ---------------------------------------------------------------------------
# Truck factor (pure logic over build_people)
# ---------------------------------------------------------------------------


def _meta(owner: str | None, path: str, bus_factor: int = 1) -> dict:
    return {"primary_owner_name": owner, "bus_factor": bus_factor, "file_path": path}


def test_truck_factor_single_dominant_owner() -> None:
    # One owner holds 8/10 files → truck factor 1.
    metas = [_meta("Ada", f"a{i}.py") for i in range(8)] + [
        _meta("Bob", "b.py"),
        _meta("Cara", "c.py"),
    ]
    out = build_people(metas)
    assert out["truck_factor"] == 1


def test_truck_factor_spread_ownership() -> None:
    # Four owners with 3/3/2/2 → need 2 to cross 50% of 10.
    metas = (
        [_meta("Ada", f"a{i}.py") for i in range(3)]
        + [_meta("Bob", f"b{i}.py") for i in range(3)]
        + [_meta("Cara", f"c{i}.py") for i in range(2)]
        + [_meta("Dan", f"d{i}.py") for i in range(2)]
    )
    out = build_people(metas)
    assert out["truck_factor"] == 2


def test_truck_factor_none_without_owners() -> None:
    out = build_people([_meta(None, "x.py", bus_factor=0)])
    assert out["truck_factor"] is None


# ---------------------------------------------------------------------------
# The commit pass on plain rows
# ---------------------------------------------------------------------------


def test_punch_card_buckets_commits() -> None:
    rows = [
        # Wednesday 2024-01-03 at 14:00 UTC: weekday 2, hour 14.
        _commit("w1", datetime(2024, 1, 3, 14, 0, tzinfo=UTC)),
        _commit("w2", datetime(2024, 1, 3, 14, 30, tzinfo=UTC)),
        _commit("s1", datetime(2024, 1, 6, 11, 0, tzinfo=UTC)),
        _commit("s2", datetime(2024, 1, 7, 12, 0, tzinfo=UTC)),
    ]
    pc = build_commit_pass(rows)["rhythm"]["punch_card"]
    assert pc["matrix"][2][14] == 2
    assert pc["peak"] == {"weekday": 2, "hour": 14, "count": 2}
    assert pc["matrix"][5][11] + pc["matrix"][6][12] == 2
    # No commit carries an offset, so the card stays in UTC.
    assert pc["timezone_mode"] == "utc"


def test_commit_pass_degrades_on_empty_repo() -> None:
    out = build_commit_pass([])
    assert out["rhythm"]["punch_card"]["total"] == 0
    assert out["rhythm"]["punch_card"]["peak"] is None
    assert out["rhythm"]["longest_streak"] is None
    assert out["rhythm"]["longest_silence"] is None
    assert out["rhythm"]["busiest_day"] is None
    assert out["rhythm"]["velocity"] is None
    assert out["chronotypes"] == []
    assert out["arrivals"] == []
    assert out["origin"]["age_days"] is None


def test_punch_card_uses_author_local_time_when_offsets_present() -> None:
    """18:30 UTC at +05:30 is midnight local, on the next weekday."""
    rows = [
        {**_commit("a1", datetime(2024, 1, 3, 18, 30)), "committed_offset_minutes": 330},
        {**_commit("a2", datetime(2024, 1, 3, 18, 45)), "committed_offset_minutes": 330},
    ]
    pc = build_commit_pass(rows)["rhythm"]["punch_card"]
    assert pc["timezone_mode"] == "author_local"
    assert pc["matrix"][3][0] == 2
    assert pc["matrix"][2][18] == 0


def test_longest_silence_and_iso_string_timestamps() -> None:
    """Artifact rows carry ISO strings; the gap is measured between commits."""
    rows = [
        _commit("a", "2024-01-01T10:00:00Z"),
        _commit("b", "2024-01-01T12:00:00+00:00"),
        _commit("c", "2024-01-04T12:00:00+00:00"),
    ]
    silence = build_commit_pass(rows)["rhythm"]["longest_silence"]
    assert silence["hours"] == 72
    assert silence["start"].startswith("2024-01-01T12:00")
