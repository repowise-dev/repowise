"""How a pipeline step reads in the terminal: done, running, or still to come.

Pure formatting, no live display: :mod:`repowise.cli.ui.progress_display`
draws the running rows and :class:`~repowise.cli.ui.progress.RichProgressCallback`
prints a done row as each step finishes. Every glyph and style a step state
uses is in :data:`STATE_MARKS`: the glyph comes from
:mod:`repowise.cli.ui.glyphs` and the style from the theme. Each state carries a
glyph as well as a colour, so it still reads with colour off.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.markup import escape

from repowise.cli.ui.brand import DIM, OK, format_elapsed
from repowise.cli.ui.glyphs import GLYPHS, glyph

DONE = "done"
PENDING = "pending"

#: state -> (glyph name, style). The running state is the live owl row.
STATE_MARKS: dict[str, tuple[str, str]] = {
    DONE: ("ok", OK),
    PENDING: ("pending", DIM),
}

#: A step running longer than this gets the long-step line.
LONG_STEP_SECONDS = 10.0

# A rate measured over less than this is mostly startup cost, and a step of a
# handful of coarse items (three drift checks, five decision sources) has no
# rate at all: its last item can take longer than the rest together. No
# estimate is better than a wrong one.
_MIN_RATE_SECONDS = 3.0
_MIN_TOTAL_ITEMS = 20
_MIN_DONE_ITEMS = 5

_SUBSTEP_PREFIX, _ = GLYPHS["substep"]
_ELLIPSIS, _ = GLYPHS["ellipsis"]


def running_label(raw: str) -> str:
    """A step label as a running row shows it: its ``↳`` and ``…`` in this terminal's form."""
    return raw.replace(_SUBSTEP_PREFIX, glyph("substep")).replace(_ELLIPSIS, glyph("ellipsis"))


def split_label(raw: str) -> tuple[str, int]:
    """``"  ↳ Resolving imports"`` -> ``("Resolving imports", 1)``; trailing ``…`` dropped.

    Takes the label in either form, as written or as :func:`running_label` made it.
    """
    text = raw.strip()
    depth = 0
    for prefix in GLYPHS["substep"]:
        if text.startswith(prefix):
            depth = 1
            text = text[len(prefix) :].strip()
            break
    for more in GLYPHS["ellipsis"]:
        text = text.removesuffix(more)
    return text.rstrip(), depth


def count_text(completed: float, total: float | None, unit: str) -> str:
    """``"1,204/2,938 files"``, or ``"1,204 files"`` with no total; empty with no unit."""
    if not unit:
        return ""
    done = int(completed)
    if total:
        return f"{done:,}/{int(total):,} {unit}"
    return f"{done:,} {unit}"


def time_left(completed: float, total: float | None, elapsed: float | None) -> float | None:
    """Seconds left at the rate measured so far, or None when there is no honest estimate.

    Needs a known total of many items, several of them finished, and a few
    seconds of measured rate. A step with an unknown total never gets one.
    """
    if not total or not elapsed or completed >= total:
        return None
    if total < _MIN_TOTAL_ITEMS or completed < _MIN_DONE_ITEMS or elapsed < _MIN_RATE_SECONDS:
        return None
    return elapsed / completed * (total - completed)


def format_time_left(seconds: float) -> str:
    """``"about 40s left"``, ``"about 3m left"``. Coarse on purpose: it is an estimate."""
    if seconds < 60:
        return f"about {max(5, round(seconds / 5) * 5)}s left"
    return f"about {round(seconds / 60)}m left"


def format_running(seconds: float | None) -> str:
    """Whole seconds while a step runs (``"12s"``, ``"1m 05s"``): tenths would flicker."""
    whole = int(seconds or 0)
    if whole >= 60:
        return f"{whole // 60}m {whole % 60:02d}s"
    return f"{whole}s"


@dataclass(frozen=True)
class FinishedStep:
    """What a done row reports about one step."""

    label: str
    completed: float = 0
    total: float | None = None
    unit: str = ""
    elapsed: float | None = None


def done_line(step: FinishedStep) -> str:
    """The markup line a finished step leaves behind: ``  ✓ Parsing files · 30 files · 0.4s``."""
    label, depth = split_label(step.label)
    name, style = STATE_MARKS[DONE]
    indent = "  " * (depth + 1)
    amount = step.total or step.completed
    count = count_text(amount, None, step.unit) if amount else ""
    facts = [f for f in (count, format_elapsed(step.elapsed or 0.0)) if f]
    sep = glyph("sep")
    tail = f" [{DIM}]{sep} {f' {sep} '.join(facts)}[/]" if facts else ""
    return f"{indent}[{style}]{glyph(name)}[/] {escape(label)}{tail}"


def long_step_line(resume_command: str) -> str:
    """Shown under a step that has run past :data:`LONG_STEP_SECONDS`."""
    return (
        f"  [{DIM}]Long step. Safe to Ctrl-C: [bold]{escape(resume_command)}[/bold] "
        "continues where it stopped.[/]"
    )


def upcoming_line(titles: list[str]) -> str:
    """The stages still to run, e.g. ``  ○ Next: Analysis · Generation · Persistence``."""
    name, style = STATE_MARKS[PENDING]
    sep = glyph("sep")
    return f"  [{style}]{glyph(name)} Next: {escape(f' {sep} '.join(titles))}[/]"
