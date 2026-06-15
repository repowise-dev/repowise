"""Pure-function tests for the health badge fields + SVG rendering."""

from __future__ import annotations

from repowise.server.routers.code_health import _badge_fields, _render_badge_svg


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
    assert 'media' not in svg  # sanity: it's markup, not a response wrapper
