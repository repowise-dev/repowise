"""Tests for the owl mascot banner (``ui/mascot.py``).

Pins the locked banner design: layout width, heatmap determinism (seeded from
the repo name), letter-aligned colour cells, spinner frame geometry, and the
Windows-safe character set.
"""

from __future__ import annotations

import unicodedata
import zlib

import pytest
from rich.console import Console

from repowise.cli.ui import mascot
from repowise.cli.ui.brand import print_banner

FULL_BANNER_MAX_COLS = 79
COMPACT_BANNER_MAX_COLS = 47


def _banner_lines(repo_name: str | None, *, compact: bool = False) -> list[str]:
    return mascot.banner_text(repo_name, compact=compact).plain.split("\n")


def test_banner_width() -> None:
    for line in _banner_lines("my-cool-repo"):
        assert len(line.rstrip()) <= FULL_BANNER_MAX_COLS


def test_banner_deterministic() -> None:
    a1 = mascot.banner_text("repo-a")
    a2 = mascot.banner_text("repo-a")
    b = mascot.banner_text("repo-b")
    assert a1.markup == a2.markup  # same repo -> identical styled render
    assert a1.markup != b.markup  # different repo -> different heat pattern
    assert a1.plain == b.plain  # ... but identical letterforms


def test_seed_is_stable_across_processes() -> None:
    # seed must come from zlib.crc32 (stable), never the salted built-in
    # hash() — pin a known value so a "simplification" to hash() fails loudly.
    assert mascot.seed_for("repowise") == zlib.crc32(b"repowise") == 3401388275
    assert mascot.seed_for(None) == mascot.seed_for("repowise")


def test_heat_grid_bounds() -> None:
    grid = mascot.heat_grid(seed=5, n_cells=30)
    assert len(grid) == 5
    for row in grid:
        assert len(row) == 30
        assert all(0 <= cell <= len(mascot.HEAT) - 1 for cell in row)


@pytest.mark.parametrize("compact", [False, True])
def test_cells_align_to_letters(compact: bool) -> None:
    _rows, cellmap = mascot.render_wordmark(compact)
    cell_w = 1 if compact else 2

    # Gap columns are unpainted; letter columns carry nonnegative cell ids.
    assert min(cellmap) == -1
    painted = [c for c in cellmap if c >= 0]
    assert painted[0] == 0
    assert sorted(set(painted)) == list(range(max(painted) + 1))

    # Cell ids never decrease left->right, each cell spans at most cell_w
    # contiguous columns, and an id never recurs after a gap (no bleed
    # across the inter-letter gap).
    last = -1
    run = 0
    after_gap: set[int] = set()
    for c in cellmap:
        if c < 0:
            after_gap.update(range(last + 1))
            continue
        assert c >= last
        run = run + 1 if c == last else 1
        assert run <= cell_w
        assert c not in after_gap
        last = c


def test_frames_are_uniform_width() -> None:
    assert all(len(frame) == 5 for frame in mascot.THINKING_FRAMES)
    assert mascot.OWL_SPINNER in ("owl", "dots")
    if mascot.OWL_SPINNER == "owl":
        from rich.spinner import Spinner

        spinner = Spinner("owl")
        assert spinner.frames == mascot.THINKING_FRAMES


def test_art_is_utf8_clean() -> None:
    art: list[str] = [
        *mascot.OWL_FULL,
        *mascot.OWL_COMPACT,
        *mascot.THINKING_FRAMES,
        mascot.mini(mascot.EYES_IDLE),
        mascot.mini(mascot.EYES_SLEEPY),
        mascot.mini(mascot.EYES_HAPPY),
        mascot.mini(mascot.EYES_ERROR),
    ]
    for font in (mascot.FONT_FULL, mascot.FONT_COMPACT):
        for glyph in font.values():
            art.extend(glyph)
    for s in art:
        s.encode("utf-8")  # must not raise
        for ch in s:
            assert unicodedata.east_asian_width(ch) not in ("W", "F")


def test_compact_banner_under_80() -> None:
    console = Console(width=70, record=True, force_terminal=True)
    print_banner(console, repo_name="my-cool-repo")
    out = console.export_text()
    assert ",___," in out  # compact owl used
    assert ",_____," not in out  # full owl absent
    for line in out.split("\n"):
        assert len(line.rstrip()) <= COMPACT_BANNER_MAX_COLS


def test_banner_width_single_source_of_truth() -> None:
    # print_banner derives its full/compact threshold from these — pin the
    # current layout so an accidental font/owl width change fails loudly.
    assert mascot.banner_width() == 78
    assert mascot.banner_width(compact=True) == 47
    for compact, width in ((False, mascot.banner_width()), (True, 47)):
        for line in _banner_lines("my-cool-repo", compact=compact):
            assert len(line.rstrip()) <= width


def test_repo_name_with_markup_is_escaped() -> None:
    # A directory name containing rich markup must render literally, not
    # crash with MarkupError or inject styling.
    console = Console(width=100, record=True, force_terminal=True)
    print_banner(console, repo_name="evil[/bold]name")
    assert "evil[/bold]name" in console.export_text()


def test_full_banner_at_wide_width() -> None:
    console = Console(width=100, record=True, force_terminal=True)
    print_banner(console, repo_name="my-cool-repo")
    out = console.export_text()
    assert ",_____," in out
    assert "codebase intelligence for developers and AI" in out
    assert "Repository: my-cool-repo" in out


@pytest.mark.parametrize("compact", [False, True])
def test_banner_colors_derive_from_repo_seed(compact: bool) -> None:
    # Behavioral pin: every painted wordmark cell in the rendered banner must
    # carry exactly the shade predicted by heat_grid(seed_for(repo), n) — for
    # BOTH sizes, so full and compact share one seed (pattern parity).
    rows, cellmap = mascot.render_wordmark(compact)
    n_cells = max(c for c in cellmap if c >= 0) + 1
    grid = mascot.heat_grid(mascot.seed_for("repo-x"), n_cells)

    text = mascot.banner_text("repo-x", compact=compact)
    owl = mascot.OWL_COMPACT if compact else mascot.OWL_FULL
    offset = 1 + max(len(line) for line in owl) + 2  # indent + owl + gap
    style_at = {span.start: str(span.style) for span in text.spans}

    checked = 0
    for x, ch in enumerate(rows[0]):
        if ch == " " or cellmap[x] < 0:
            continue
        assert style_at[offset + x] == f"bold {mascot.HEAT[grid[0][cellmap[x]]]}"
        checked += 1
    assert checked > 20  # the whole top row of the wordmark was verified
