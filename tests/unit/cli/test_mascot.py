"""Tests for the owl mascot (``ui/mascot.py``): banner, one-line header, mini owl.

Assert the thing you would defend in review (the layout, the one accent, the
fallback), not the thing the implementation happens to do.
"""

from __future__ import annotations

import unicodedata

from rich.console import Console

from repowise.cli import _stdio
from repowise.cli.ui import mascot
from repowise.cli.ui.brand import BRAND_STYLE, DIM, OWL, THEME, print_banner

BANNER_COLS = 53

EXPECTED_BANNER = [
    "  ,___,   ┬─┐┌─┐┌─┐┌─┐┬ ┬┬┌─┐┌─┐",
    "  {◉,◉}   ├┬┘├┤ ├─┘│ │││││└─┐├┤",
    "  ( ▼ )   ┴└─└─┘┴  └─┘└┴┘┴└─┘└─┘",
    "  /)_(\\   codebase intelligence for developers and AI",
    '   " "    v1.2.3',
]


def _banner_lines() -> list[str]:
    return [line.rstrip() for line in mascot.banner_text("1.2.3").plain.split("\n")]


def test_banner_layout() -> None:
    assert _banner_lines() == EXPECTED_BANNER


def test_wordmark_rows_are_rectangular_and_set_solid() -> None:
    rows = mascot.render_wordmark()
    assert len(rows) == 3
    assert {len(row) for row in rows} == {22}
    for char in mascot.FONT.values():
        assert len({len(row) for row in char}) == 1, "ragged letters skew the wordmark"


def test_banner_width_is_a_single_source_of_truth() -> None:
    assert mascot.banner_width() == BANNER_COLS
    assert max(len(line) for line in _banner_lines()) == BANNER_COLS


def test_banner_is_one_accent_with_cream_strokes() -> None:
    # Wordmark and eyes orange, owl strokes cream, tagline and version dim.
    styles = {str(span.style) for span in mascot.banner_text("1.2.3").spans}
    assert styles == {BRAND_STYLE, OWL, DIM}


def test_banner_is_deterministic() -> None:
    first, second = mascot.banner_text("1.2.3"), mascot.banner_text("1.2.3")
    assert first.plain == second.plain
    assert first.spans == second.spans


def test_frames_are_uniform_width() -> None:
    assert all(len(frame) == 5 for frame in mascot.THINKING_FRAMES)
    for tick in range(len(mascot.THINKING_FRAMES)):
        frame = mascot.thinking_text(tick * 0.18 + 0.01)
        assert frame.plain == mascot.THINKING_FRAMES[tick]
        assert frame.cell_len == 5


def test_the_mini_owl_is_the_banner_owls_face() -> None:
    assert mascot.mini() == mascot.OWL[1].strip()


def test_art_is_single_width() -> None:
    # A wide character shifts the whole lockup by a column on the terminals
    # that render it wide.
    art: list[str] = [
        *mascot.OWL,
        *mascot.THINKING_FRAMES,
        *(mascot.mini(state) for state in mascot.EYES),
        *mascot.render_wordmark(),
    ]
    for s in art:
        for ch in s:
            assert unicodedata.east_asian_width(ch) not in ("W", "F")


def _printed(width: int, repo_name: str | None = "my-cool-repo") -> str:
    console = Console(width=width, record=True, force_terminal=True, theme=THEME)
    print_banner(console, repo_name=repo_name)
    return console.export_text()


def test_wide_terminal_gets_the_banner() -> None:
    out = _printed(80)
    assert ",___," in out
    assert "codebase intelligence for developers and AI" in out
    assert "Repository: my-cool-repo" in out


def test_narrow_terminal_gets_the_one_line_header() -> None:
    out = _printed(59)
    assert ",___," not in out
    assert "{◉,◉} repowise  v" in out
    # The tagline is dropped rather than wrapped.
    assert "codebase intelligence" not in out
    assert all(len(line) <= 59 for line in out.splitlines())


def test_header_keeps_the_tagline_when_it_fits() -> None:
    header = mascot.header_text("1.2.3", 100).plain
    assert header == " {◉,◉} repowise  v1.2.3 · codebase intelligence for developers and AI"


def test_ascii_terminal_gets_the_ascii_header(monkeypatch) -> None:
    monkeypatch.setattr(_stdio, "_original_stdout_encoding", "cp1252")
    out = _printed(100)
    assert out.isascii()
    assert "{o,o} repowise  v" in out
    assert " - codebase intelligence for developers and AI" in out


def test_repo_name_with_markup_is_escaped() -> None:
    assert "evil[/bold]name" in _printed(100, repo_name="evil[/bold]name")


def test_a_workspace_is_labelled_once() -> None:
    console = Console(width=100, record=True, force_terminal=True, theme=THEME)
    print_banner(console, repo_name="ws", workspace=True)
    out = console.export_text()
    assert "Workspace: ws" in out
    assert "Repository:" not in out
