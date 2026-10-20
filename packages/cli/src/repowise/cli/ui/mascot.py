"""Owl mascot: banner art, the one-line header, eye states, and the wordmark.

Single home for every piece of the init-banner identity: the owl, the REPOWISE
wordmark, the mini owl and its states, and the spinner that blinks while
repowise thinks. All render functions are pure and deterministic.

Colours: owl strokes cream, eyes and wordmark brand orange, tagline dim. The
wordmark is one colour on purpose: decoration that carries no meaning gets one
accent, not a ramp.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console, ConsoleOptions, RenderResult
from rich.live import Live
from rich.text import Text

from repowise.cli.ui.brand import BRAND_STYLE, DIM
from repowise.cli.ui.brand import OWL as OWL_STYLE
from repowise.cli.ui.glyphs import ascii_only, glyph

TAGLINE = "codebase intelligence for developers and AI"

# ---------------------------------------------------------------------------
# Owl
# ---------------------------------------------------------------------------

OWL = [
    " ,___,",
    " {◉,◉}",
    " ( ▼ )",
    " /)_(\\",
    '  " " ',
]

# The mini owl is the big owl's face line. Its eyes say how the run went, so a
# state reads with colour off. state -> (unicode eyes, ascii eyes).
IDLE = "idle"
BLINK = "blink"
SHUT = "shut"
DONE = "done"
INTERRUPTED = "interrupted"
WARNED = "warn"
FAILED = "failed"

EYES: dict[str, tuple[str, str]] = {
    IDLE: ("◉", "o"),
    BLINK: ("◒", "o"),
    SHUT: ("─", "o"),
    DONE: ("^", "^"),
    INTERRUPTED: ("─", "-"),
    WARNED: ("•", "."),
    FAILED: ("×", "x"),
}


def _eyes(state: str) -> str:
    unicode, ascii_form = EYES[state]
    return ascii_form if ascii_only() else unicode


def mini(state: str = IDLE) -> str:
    """The mini owl face for *state*, e.g. ``{◉,◉}``. Always 5 columns."""
    eyes = _eyes(state)
    return f"{{{eyes},{eyes}}}"


def mini_text(state: str = IDLE) -> Text:
    """:func:`mini` with cream strokes and orange eyes."""
    eyes = _eyes(state)
    return Text.assemble(
        ("{", OWL_STYLE),
        (eyes, BRAND_STYLE),
        (",", OWL_STYLE),
        (eyes, BRAND_STYLE),
        ("}", OWL_STYLE),
    )


# The spinner is a slow blink, not a spin. A continuous eye-roll at 5fps reads
# as a cheap loading GIF and draws the eye away from the progress bar next to
# it; holding the eyes open and closing them once per cycle reads as alive
# without competing for attention. Motion lives in the last three frames.
_BLINK_CYCLE = [IDLE] * 7 + [BLINK, SHUT, BLINK]
_FRAME_SECONDS = 0.18

THINKING_FRAMES = [mini(state) for state in _BLINK_CYCLE]


def thinking_text(time: float) -> Text:
    """The spinner frame for *time*, in seconds."""
    return mini_text(_BLINK_CYCLE[int(time / _FRAME_SECONDS) % len(_BLINK_CYCLE)])


class OwlSpinner:
    """The blinking mini owl in front of a status line; see :func:`owl_status`."""

    def __init__(self, text: str = "") -> None:
        self.text = Text.from_markup(text)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        frame = thinking_text(console.get_time())
        yield Text.assemble(frame, " ", self.text) if self.text else frame


def owl_status(console: Console, text: str, **live_kwargs: Any) -> Live:
    """``console.status`` with the owl: a transient line that blinks while work runs.

    Built the way Rich builds a status, so it behaves the same on a pipe.
    """
    return Live(
        OwlSpinner(text),
        console=console,
        refresh_per_second=12.5,
        transient=True,
        **live_kwargs,
    )


# ---------------------------------------------------------------------------
# Wordmark: thin box-drawing letters, three rows, beside the owl's top three.
# ---------------------------------------------------------------------------

FONT = {
    "R": ["┬─┐", "├┬┘", "┴└─"],
    "E": ["┌─┐", "├┤ ", "└─┘"],
    "P": ["┌─┐", "├─┘", "┴  "],
    "O": ["┌─┐", "│ │", "└─┘"],
    "W": ["┬ ┬", "│││", "└┴┘"],
    "I": ["┬", "│", "┴"],
    "S": ["┌─┐", "└─┐", "└─┘"],
}

_WORD = "REPOWISE"
_OWL_GAP = 3
_INDENT = 1


def render_wordmark() -> tuple[str, ...]:
    """The three wordmark rows, letters set solid."""
    return tuple("".join(FONT[char][r] for char in _WORD) for r in range(3))


def banner_width() -> int:
    """Total rendered banner width in columns: indent, owl, gap, then the widest text."""
    text_width = max(len(render_wordmark()[0]), len(TAGLINE))
    return _INDENT + max(len(line) for line in OWL) + _OWL_GAP + text_width


def _paint_owl(line: str) -> Text:
    """Owl strokes in cream, eyes in brand orange."""
    out = Text()
    for ch in line:
        if ch == EYES[IDLE][0]:
            out.append(ch, style=BRAND_STYLE)
        elif ch == " ":
            out.append(" ")
        else:
            out.append(ch, style=OWL_STYLE)
    return out


def banner_text(version: str = "") -> Text:
    """The owl beside the wordmark, the tagline and the version. Unicode only."""
    owl_w = max(len(line) for line in OWL)
    right: list[tuple[str, str]] = [(row, BRAND_STYLE) for row in render_wordmark()]
    right.append((TAGLINE, DIM))
    right.append((f"v{version}" if version else "", DIM))
    out = Text()
    for r, (text, style) in enumerate(right):
        if r:
            out.append("\n")
        out.append(" " * _INDENT)
        out.append_text(_paint_owl(OWL[r].ljust(owl_w)))
        out.append(" " * _OWL_GAP)
        out.append(text, style=style)
    out.rstrip()
    return out


def header_text(version: str, width: int | None = None) -> Text:
    """The one-line header: ``{◉,◉} repowise  v0.57.0 · codebase intelligence ...``.

    For a terminal narrower than the banner, or one that cannot show it. The
    tagline is dropped when the line would not fit in *width* columns.
    """
    line = Text.assemble(" ", mini_text(IDLE), " ", ("repowise", BRAND_STYLE), "  ")
    full = f"v{version} {glyph('sep')} {TAGLINE}"
    fits = width is None or line.cell_len + len(full) <= width
    line.append(full if fits else f"v{version}", style=DIM)
    return line
