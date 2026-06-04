"""Owl mascot: banner art, state frames, and the heatmap wordmark.

Single home for every piece of the init-banner identity: the owl, the
REPOWISE block wordmark (full + compact variants), the GitHub-heatmap
colouring, and the spinner frames used while repowise thinks.

All render functions are pure and deterministic — the heatmap pattern is
seeded from the repo name, so the same repo always gets the same banner.
"""

from __future__ import annotations

import zlib
from functools import cache
from random import Random

from rich.text import Text

from repowise.cli.ui.brand import BRAND

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

WHITE = "#E8E8E8"  # owl body strokes (logo white)

# Floored 5-shade heatmap ramp: deep ember → blazing. No near-black shade so
# the wordmark always reads, even at the cold end of the gradient.
HEAT = ["#5C3208", "#9C5710", "#D97A16", "#F59520", "#FFC06A"]

# ---------------------------------------------------------------------------
# Owl
# ---------------------------------------------------------------------------

OWL_FULL = [
    " ,_____,",
    " (◉ , ◉)",
    " (  ▼  )",
    " /)___(\\",
    '   " "  ',
]

OWL_COMPACT = [
    " ,___,",
    " (◉,◉)",
    " ( ▼ )",
    " /)_(\\",
    '  " " ',
]

EYES_THINKING = ["◐", "◓", "◑", "◒"]  # eye-roll cycle
EYES_IDLE = "◉"
EYES_SLEEPY = "─"
EYES_HAPPY = "^"
EYES_ERROR = "x"


def mini(eyes: str = EYES_IDLE) -> str:
    """Single-line mini owl face, e.g. ``{◉ ◉}`` — always 5 chars wide."""
    return "{" + eyes + " " + eyes + "}"


THINKING_FRAMES = [mini(e) for e in EYES_THINKING]

# ---------------------------------------------------------------------------
# Spinner registration — ``rich._spinners.SPINNERS`` is private API (a plain
# dict consumed by ``rich.spinner.Spinner``), so registration is wrapped: if a
# future rich version moves it, everything silently stays on "dots". Always
# reference ``OWL_SPINNER`` at call sites, never the literal ``"owl"``.
# ---------------------------------------------------------------------------

try:
    from rich._spinners import SPINNERS

    SPINNERS["owl"] = {"interval": 200, "frames": THINKING_FRAMES}
    OWL_SPINNER = "owl"
except Exception:  # pragma: no cover — depends on rich internals
    OWL_SPINNER = "dots"

# ---------------------------------------------------------------------------
# Wordmark fonts — half-block finished letterforms (R tail = stepped taper).
# FONT_FULL uses 2-char stroke cells; FONT_COMPACT is the same design at
# 1-char strokes for narrow terminals.
# ---------------------------------------------------------------------------

FONT_FULL = {
    "R": ["██████▄", "██   ██", "██████▀", "██ ▀█▄ ", "██  ▀█▄"],
    "E": ["██████", "██    ", "█████ ", "██    ", "██████"],
    "P": ["██████▄", "██   ██", "██████▀", "██     ", "██     "],
    "O": ["▄█████▄", "██   ██", "██   ██", "██   ██", "▀█████▀"],
    "W": ["██     ██", "██     ██", "██  █  ██", "██ ███ ██", "▀██▀ ▀██▀"],
    "I": ["████", " ██ ", " ██ ", " ██ ", "████"],
    "S": ["▄█████▄", "██     ", "▀█████▄", "     ██", "▀█████▀"],
}

FONT_COMPACT = {
    "R": ["███▄ ", "█  █ ", "███▀ ", "█ ▀▄ ", "█  ▀▄"],
    "E": ["███", "█  ", "██ ", "█  ", "███"],
    "P": ["███▄", "█  █", "███▀", "█   ", "█   "],
    "O": ["▄██▄", "█  █", "█  █", "█  █", "▀██▀"],
    "W": ["█   █", "█   █", "█ █ █", "█ █ █", "▀█▀█▀"],
    "I": ["███", " █ ", " █ ", " █ ", "███"],
    "S": ["▄██▄", "█   ", "▀██▄", "   █", "▀██▀"],
}

_WORD = "REPOWISE"


@cache
def render_wordmark(compact: bool = False) -> tuple[tuple[str, ...], tuple[int, ...]]:
    """Wordmark rows + per-column heat-cell ids.

    Cell ids are aligned to each letter's own grid (one cell = ``cell_w``
    columns starting at the letter's left edge) so every coloured unit is a
    true square — a cell never slices a stroke or bleeds across the
    inter-letter gap. Gap columns map to ``-1`` (unpainted).
    """
    font = FONT_COMPACT if compact else FONT_FULL
    cell_w = 1 if compact else 2
    gap = 1 if compact else 2

    rows = ["", "", "", "", ""]
    cellmap: list[int] = []
    cell_base = 0
    for li, ch in enumerate(_WORD):
        glyph = font[ch]
        width = len(glyph[0])
        n_cells = (width + cell_w - 1) // cell_w
        for lx in range(width):
            cellmap.append(cell_base + min(lx // cell_w, n_cells - 1))
        cell_base += n_cells
        for r in range(5):
            rows[r] += glyph[r]
        if li < len(_WORD) - 1:
            for r in range(5):
                rows[r] += " " * gap
            cellmap.extend([-1] * gap)
    return tuple(rows), tuple(cellmap)


def banner_width(compact: bool = False) -> int:
    """Total rendered banner width in columns (indent + owl + gap + wordmark).

    Single source of truth for layout maths — ``print_banner`` derives its
    full/compact switch threshold from this, so font or owl changes can't
    silently desync the two.
    """
    owl = OWL_COMPACT if compact else OWL_FULL
    rows, _ = render_wordmark(compact)
    return 1 + max(len(line) for line in owl) + 2 + len(rows[0])


def seed_for(repo_name: str | None) -> int:
    """Stable cross-process seed for a repo's heatmap pattern.

    Uses ``zlib.crc32`` deliberately — the built-in ``hash()`` is salted per
    process, which would give every run a different banner.
    """
    return zlib.crc32((repo_name or "repowise").encode())


def heat_grid(seed: int, n_cells: int) -> list[list[int]]:
    """Gradient + jitter shade grid (5 rows by *n_cells*), deterministic.

    Base shade sweeps linearly left→right across cells; each cell jitters
    ±1 shade around the sweep, clamped to the palette bounds.
    """
    rng = Random(seed)
    span = max(n_cells - 1, 1)
    grid = [[0] * n_cells for _ in range(5)]
    for r in range(5):
        for c in range(n_cells):
            base = (c / span) * (len(HEAT) - 1)
            idx = round(base + rng.uniform(-1.1, 1.1))
            grid[r][c] = min(max(idx, 0), len(HEAT) - 1)
    return grid


def _paint_owl(line: str) -> Text:
    """Owl strokes in logo white, eyes in brand orange."""
    out = Text()
    for ch in line:
        if ch == EYES_IDLE:
            out.append(ch, style=f"bold {BRAND}")
        elif ch == " ":
            out.append(" ")
        else:
            out.append(ch, style=f"bold {WHITE}")
    return out


def banner_text(repo_name: str | None, compact: bool = False) -> Text:
    """Compose owl + heatmap wordmark into a single renderable ``Text``."""
    owl = OWL_COMPACT if compact else OWL_FULL
    rows, cellmap = render_wordmark(compact)
    n_cells = max(c for c in cellmap if c >= 0) + 1
    grid = heat_grid(seed_for(repo_name), n_cells)

    owl_w = max(len(line) for line in owl)
    out = Text()
    for r in range(5):
        if r:
            out.append("\n")
        out.append(" ")
        out.append_text(_paint_owl(owl[r].ljust(owl_w)))
        out.append("  ")
        for x, ch in enumerate(rows[r]):
            if ch == " " or cellmap[x] < 0:
                out.append(" ")
            else:
                out.append(ch, style=f"bold {HEAT[grid[r][cellmap[x]]]}")
    return out
