"""Pure-function tests for the health badge fields + SVG rendering, plus the
per-file trend serializer's wire shape."""

from __future__ import annotations

from repowise.core.analysis.health.signals import file_signals
from repowise.core.analysis.health.trends import FileTrend, FileTrendPoint
from repowise.server.routers.code_health import (
    _badge_fields,
    _file_signals_to_dict,
    _file_trend_to_dict,
    _render_badge_svg,
)


def test_badge_fields_band_colors() -> None:
    assert _badge_fields(9.0) == ("health", "9.0/10", "brightgreen", "healthy")
    assert _badge_fields(6.0) == ("health", "6.0/10", "yellow", "warning")
    assert _badge_fields(2.0) == ("health", "2.0/10", "red", "alert")


def test_badge_fields_no_data() -> None:
    _label, message, color, band = _badge_fields(None)
    assert message == "no data"
    assert band == "unknown"
    assert color == "lightgrey"


def test_render_badge_svg_embeds_label_and_message() -> None:
    svg = _render_badge_svg("health", "7.4/10", "brightgreen")
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert "7.4/10" in svg
    assert "#4c1" in svg  # brightgreen hex
    assert "media" not in svg  # sanity: it's markup, not a response wrapper


def test_file_trend_to_dict_thin_history() -> None:
    t = FileTrend(
        file_path="a.py",
        points=[],
        current=None,
        previous=None,
        delta=None,
        declining=False,
        snapshot_count=1,
    )
    d = _file_trend_to_dict(t)
    assert d == {
        "file_path": "a.py",
        "points": [],
        "current": None,
        "previous": None,
        "delta": None,
        "declining": False,
        "snapshot_count": 1,
    }


def test_file_trend_to_dict_serializes_points() -> None:
    from datetime import UTC, datetime

    t = FileTrend(
        file_path="a.py",
        points=[
            FileTrendPoint(taken_at=datetime(2026, 1, 1, tzinfo=UTC), score=8.0),
            FileTrendPoint(taken_at=None, score=6.5),
        ],
        current=6.5,
        previous=8.0,
        delta=-1.5,
        declining=True,
        snapshot_count=2,
    )
    d = _file_trend_to_dict(t)
    assert d["points"][0]["taken_at"] == "2026-01-01T00:00:00+00:00"
    assert d["points"][0]["score"] == 8.0
    assert d["points"][1]["taken_at"] is None
    assert d["delta"] == -1.5
    assert d["declining"] is True


class _Git:
    """Stub git-metadata row for the signals serializer."""

    def __init__(self, **kw: object) -> None:
        defaults = dict(
            prior_defect_count=0,
            change_entropy_pct=0.0,
            lines_added_90d=0,
            lines_deleted_90d=0,
            commit_count_90d=0,
            age_days=0,
            primary_owner_name=None,
            primary_owner_commit_pct=None,
            recent_owner_name=None,
            recent_owner_commit_pct=None,
        )
        defaults.update(kw)
        self.__dict__.update(defaults)


def test_file_signals_to_dict_no_data_all_null() -> None:
    d = _file_signals_to_dict(file_signals(None, None))
    assert d == {
        "prior_defect_count": None,
        "change_entropy_pct": None,
        "lines_added_90d": None,
        "lines_deleted_90d": None,
        "commit_count_90d": None,
        "age_days": None,
        "primary_owner_name": None,
        "primary_owner_commit_pct": None,
        "recent_owner_name": None,
        "recent_owner_commit_pct": None,
        "in_degree": None,
        "out_degree": None,
    }


def test_file_signals_to_dict_populated_and_normalized() -> None:
    git = _Git(prior_defect_count=2, change_entropy_pct=0.5, primary_owner_name="Ada")
    d = _file_signals_to_dict(file_signals(git, {"in_degree": 9, "out_degree": 4}))
    assert d["prior_defect_count"] == 2
    assert d["change_entropy_pct"] == 50.0  # 0-1 column → 0-100 wire
    assert d["primary_owner_name"] == "Ada"
    assert d["in_degree"] == 9
    assert d["out_degree"] == 4
