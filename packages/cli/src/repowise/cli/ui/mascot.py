"""Owl mascot: banner art, eye states, and the block wordmark.

Single home for every piece of the init-banner identity: the owl, the REPOWISE
block wordmark, and the spinner frames used while repowise thinks.

All render functions are pure and deterministic, and there is exactly one size
of everything. The banner used to ship two — a full variant at 2-char strokes
and a compact one at 1 — with the caller hardcoding compact and the width maths
measuring full, so on an 80-column terminal the tagline shortened to fit art
that was 47 columns wide. Nothing rendered the full art in its whole life, so it
is gone rather than fixed: one size cannot desync from itself.

The wordmark is painted in the one brand colour. It used to run a five-shade
"heatmap" ramp that swept left to right and then jittered each cell by ±1.1
shades. Across the 31-cell wordmark the sweep moves 0.133 shades per cell while
the jitter moves up to 2.2, so the gradient never survived: counted over the
real grid, all 155 painted cells landed near-uniformly across the five shades.
It rendered as orange static in the first thing a user ever sees, which is the
"decorate only what does something" rule with nothing behind the paint.
"""

from __future__ import annotations

from functools import cache

from rich.text import Text

from repowise.cli.ui.brand import BRAND

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

WHITE = "#E8E8E8"  # owl body strokes (logo white)

# ---------------------------------------------------------------------------
# Owl
# ---------------------------------------------------------------------------

OWL = [
    " ,___,",
    " (◉,◉)",
    " ( ▼ )",
    " /)_(\\",
    '  " " ',
]

EYES_IDLE = "◉"
EYES_SLEEPY = "─"  # interrupt message
EYES_HAPPY = "^"  # completion panels


def mini(eyes: str = EYES_IDLE) -> str:
    """Single-line mini owl face, e.g. ``{◉ ◉}`` — always 5 chars wide."""
    return "{" + eyes + " " + eyes + "}"


# The spinner is a slow blink, not a spin. A continuous eye-roll at 5fps reads
# as a cheap loading GIF and draws the eye away from the progress bar next to
# it; holding the eyes open and closing them once per cycle reads as alive
# without competing for attention. Motion lives in the last three frames.
_BLINK_CYCLE = [EYES_IDLE] * 7 + ["◒", EYES_SLEEPY, "◒"]

THINKING_FRAMES = [mini(e) for e in _BLINK_CYCLE]

# ---------------------------------------------------------------------------
# Spinner registration — ``rich._spinners.SPINNERS`` is private API (a plain
# dict consumed by ``rich.spinner.Spinner``), so registration is wrapped: if a
# future rich version moves it, everything silently stays on "dots". Always
# reference ``OWL_SPINNER`` at call sites, never the literal ``"owl"``.
# ---------------------------------------------------------------------------

try:
    from rich._spinners import SPINNERS

    SPINNERS["owl"] = {"interval": 180, "frames": THINKING_FRAMES}
    OWL_SPINNER = "owl"
except Exception:  # pragma: no cover — depends on rich internals
    OWL_SPINNER = "dots"

# ---------------------------------------------------------------------------
# Wordmark font — half-block letterforms at 1-char strokes (R tail = stepped
# taper).
# ---------------------------------------------------------------------------

FONT = {
    "R": ["███▄ ", "█  █ ", "███▀ ", "█ ▀▄ ", "█  ▀▄"],
    "E": ["███", "█  ", "██ ", "█  ", "███"],
    "P": ["███▄", "█  █", "███▀", "█   ", "█   "],
    "O": ["▄██▄", "█  █", "█  █", "█  █", "▀██▀"],
    "W": ["█   █", "█   █", "█ █ █", "█ █ █", "▀█▀█▀"],
    "I": ["███", " █ ", " █ ", " █ ", "███"],
    "S": ["▄██▄", "█   ", "▀██▄", "   █", "▀██▀"],
}

_WORD = "REPOWISE"
_LETTER_GAP = 1
_OWL_GAP = 2
_INDENT = 1


@cache
def render_wordmark() -> tuple[str, ...]:
    """The five wordmark rows, letters separated by a single column."""
    rows = ["", "", "", "", ""]
    for index, char in enumerate(_WORD):
        glyph = FONT[char]
        for r in range(5):
            rows[r] += glyph[r]
            if index < len(_WORD) - 1:
                rows[r] += " " * _LETTER_GAP
    return tuple(rows)


def banner_width() -> int:
    """Total rendered banner width in columns (indent + owl + gap + wordmark).

    Single source of truth for layout maths, and now actually single: the
    tagline switch in ``print_banner`` calls this, and there is no second
    variant left for it to measure by mistake.
    """
    return _INDENT + max(len(line) for line in OWL) + _OWL_GAP + len(render_wordmark()[0])


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


def banner_text() -> Text:
    """Compose owl + wordmark into a single renderable ``Text``."""
    rows = render_wordmark()
    owl_w = max(len(line) for line in OWL)
    out = Text()
    for r in range(5):
        if r:
            out.append("\n")
        out.append(" " * _INDENT)
        out.append_text(_paint_owl(OWL[r].ljust(owl_w)))
        out.append(" " * _OWL_GAP)
        out.append(rows[r], style=f"bold {BRAND}")
    return out
