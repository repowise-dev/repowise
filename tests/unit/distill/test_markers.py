"""Unit tests for omission-marker rendering and parsing."""

from __future__ import annotations

import pytest

from repowise.core.distill.markers import (
    MARKER_RE,
    is_valid_ref,
    parse_marker_refs,
    render_marker,
)

REF = "3f9c2ab8d1e0"


def test_render_parse_roundtrip() -> None:
    marker = render_marker(REF, 412, 3120)
    assert parse_marker_refs(f"some output\n\n{marker}\n") == [REF]


def test_marker_is_ascii_only() -> None:
    """Distilled output is echoed to cp1252 consoles; the marker must survive."""
    marker = render_marker(REF, 10, 100)
    marker.encode("ascii")  # raises if not


def test_marker_mentions_expand_command() -> None:
    marker = render_marker(REF, 10, 100)
    assert f"repowise expand {REF}" in marker
    assert "10 lines omitted" in marker
    assert "~100 tokens" in marker


def test_parse_multiple_refs_in_order_deduped() -> None:
    a = render_marker("a" * 12, 1, 1)
    b = render_marker("b" * 12, 2, 2)
    text = f"{a}\nmiddle\n{b}\nagain {a}"
    assert parse_marker_refs(text) == ["a" * 12, "b" * 12]


def test_parse_ignores_non_marker_brackets() -> None:
    assert parse_marker_refs("[not a marker] [repowise#xyz: nope]") == []


def test_render_rejects_invalid_ref() -> None:
    with pytest.raises(ValueError):
        render_marker("XYZ", 1, 1)
    with pytest.raises(ValueError):
        render_marker("abc", 1, 1)  # too short


def test_negative_counts_clamped() -> None:
    marker = render_marker(REF, -5, -10)
    assert "0 lines omitted" in marker
    assert "~0 tokens" in marker


def test_is_valid_ref() -> None:
    assert is_valid_ref(REF)
    assert not is_valid_ref(REF.upper())
    assert not is_valid_ref(REF + "0")
    assert not is_valid_ref("")


def test_marker_regex_matches_rendered_form() -> None:
    assert MARKER_RE.search(render_marker(REF, 3, 30))
