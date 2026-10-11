"""Tests for the owl mascot banner (``ui/mascot.py``).

Pins the locked banner design: one size, one accent, spinner frame geometry,
and the Windows-safe character set.

The heatmap tests these replace are gone with the heatmap. They pinned a
five-shade ramp that swept left to right and jittered each cell by ±1.1 shades
— which, across the 31-cell wordmark, moved 0.133 shades of sweep against up to
2.2 of jitter, so what shipped was uniform orange static rather than a gradient.
The tests were correct about the code and the code was wrong about the design,
which is worth remembering before pinning a visual detail again: assert the
thing you would defend in review, not the thing the implementation happens to
do.
"""

from __future__ import annotations

import unicodedata

from rich.console import Console

from repowise.cli.ui import mascot
from repowise.cli.ui.brand import BRAND, print_banner

BANNER_MAX_COLS = 47


def _banner_lines() -> list[str]:
    return mascot.banner_text().plain.split("\n")


def test_banner_fits_its_declared_width() -> None:
    for line in _banner_lines():
        assert len(line.rstrip()) <= BANNER_MAX_COLS


def test_banner_is_deterministic_and_repo_independent() -> None:
    # The banner takes no arguments at all now, so there is nothing left for a
    # per-repo signature to key on. Two calls are byte-identical, styles too.
    first = mascot.banner_text()
    second = mascot.banner_text()
    assert first.plain == second.plain
    assert first.markup == second.markup


def test_wordmark_is_one_accent() -> None:
    # Rule 2: one accent. Every styled span in the banner is either the brand
    # orange (wordmark + eyes) or the logo white (owl strokes) — no ramp, no
    # third hue.
    styles = {str(span.style) for span in mascot.banner_text().spans}
    assert styles == {f"bold {BRAND}", f"bold {mascot.WHITE}"}


def test_frames_are_uniform_width() -> None:
    assert all(len(frame) == 5 for frame in mascot.THINKING_FRAMES)
    assert mascot.OWL_SPINNER in ("owl", "dots")
    if mascot.OWL_SPINNER == "owl":
        from rich.spinner import Spinner

        spinner = Spinner("owl")
        assert spinner.frames == mascot.THINKING_FRAMES


def test_art_is_utf8_clean() -> None:
    # Every glyph must be single-width: a wide character shifts the whole
    # lockup by a column on the terminals that render it wide.
    art: list[str] = [
        *mascot.OWL,
        *mascot.THINKING_FRAMES,
        mascot.mini(mascot.EYES_IDLE),
        mascot.mini(mascot.EYES_SLEEPY),
        mascot.mini(mascot.EYES_HAPPY),
    ]
    for glyph in mascot.FONT.values():
        art.extend(glyph)
    for s in art:
        s.encode("utf-8")  # must not raise
        for ch in s:
            assert unicodedata.east_asian_width(ch) not in ("W", "F")


def test_wordmark_rows_are_rectangular() -> None:
    rows = mascot.render_wordmark()
    assert len(rows) == 5
    assert len({len(row) for row in rows}) == 1, "ragged rows would skew the lockup"


def test_banner_width_is_a_single_source_of_truth() -> None:
    # The whole point of deleting the second size: `print_banner` derives its
    # tagline threshold from this number, and there is no other variant left for
    # it to measure by mistake. It used to measure 78 (the full art) while
    # drawing 47 (the compact art), so an 80-column terminal — the most common
    # width there is — got the short tagline for no reason.
    assert mascot.banner_width() == BANNER_MAX_COLS
    for line in _banner_lines():
        assert len(line.rstrip()) <= mascot.banner_width()


def test_long_tagline_appears_at_eighty_columns() -> None:
    # The regression the single source of truth exists to prevent.
    console = Console(width=80, record=True, force_terminal=True)
    print_banner(console, repo_name="my-cool-repo")
    out = console.export_text()
    assert "codebase intelligence for developers and AI" in out


def test_short_tagline_below_the_banner_width() -> None:
    console = Console(width=40, record=True, force_terminal=True)
    print_banner(console, repo_name="my-cool-repo")
    out = console.export_text()
    assert "codebase intelligence ·" in out
    assert "for developers and AI" not in out


def test_banner_renders_the_owl_and_repo_name() -> None:
    console = Console(width=100, record=True, force_terminal=True)
    print_banner(console, repo_name="my-cool-repo")
    out = console.export_text()
    assert ",___," in out
    assert "Repository: my-cool-repo" in out
    # Only the art is held to the banner width. The tagline is allowed past it
    # — that is the whole point of `_BANNER_WIDTH_MARGIN`, which picks the long
    # tagline whenever the terminal can carry it.
    art = [line for line in out.split("\n") if "█" in line]
    assert len(art) == 5
    for line in art:
        assert len(line.rstrip()) <= BANNER_MAX_COLS


def test_repo_name_with_markup_is_escaped() -> None:
    # A directory name containing rich markup must render literally, not
    # crash with MarkupError or inject styling.
    console = Console(width=100, record=True, force_terminal=True)
    print_banner(console, repo_name="evil[/bold]name")
    assert "evil[/bold]name" in console.export_text()
