"""Brand constants and the shared rendering primitives every init screen uses.

Four primitives live here, and they are here rather than beside their first
caller because each one had two or three near-copies before it did:

* :func:`print_banner` — the owl lockup.
* :func:`print_phase_header` — the full-width rule between pipeline phases.
* :func:`print_section` — the unbordered section opener (brand heading, dim
  blurb). Advanced config had it as a private helper; the readout screens that
  should never have been panels now use the same one.
* :func:`key_value_table` — the two-column label/value table. Rich owns the
  gutter, which is the whole point: every hand-padded version of this drifted
  by a column the moment a label grew.

The colour vocabulary is four names and no more. ``cyan`` used to be a de-facto
fifth on fourteen call sites, always on a machine-produced token — a model name,
an env var, a language code. That is what mono is for, so those sites take
``VALUE`` and the palette stays closed.
"""

from __future__ import annotations

import math

from rich.console import Console
from rich.markup import escape
from rich.rule import Rule
from rich.table import Table

# ---------------------------------------------------------------------------
# Brand / theme
# ---------------------------------------------------------------------------

BRAND = "#F59520"
BRAND_STYLE = f"bold {BRAND}"
DIM = "dim"
OK = "green"
WARN = "yellow"
ERR = "bold red"

#: Anything a machine produced and a human might retype: a model id, an env var
#: name, a path, an embedder. Rule 5 of the design language — the distinction
#: between a measured fact and written prose is carried by the face, not by
#: spending a colour on it.
VALUE = "bold"

# ---------------------------------------------------------------------------
# Banner  —  owl mascot + wordmark (art lives in ui/mascot.py)
# ---------------------------------------------------------------------------

# Breathing room required beyond the rendered banner width before we pick the
# long tagline; below that we use the short one.
_BANNER_WIDTH_MARGIN = 4


def print_banner(console: Console, repo_name: str | None = None) -> None:
    """Print the repowise owl banner, tagline, and optional repo name."""
    from repowise.cli import __version__
    from repowise.cli.ui import mascot

    narrow = console.width < mascot.banner_width() + _BANNER_WIDTH_MARGIN
    console.print()
    console.print(mascot.banner_text())
    console.print()
    if narrow:
        console.print(f" [dim]codebase intelligence · v{__version__}[/dim]", highlight=False)
    else:
        console.print(
            f" [dim]codebase intelligence for developers and AI · v{__version__}[/dim]",
            highlight=False,
        )
    if repo_name:
        console.print()
        console.print(f" Repository: [bold]{escape(repo_name)}[/bold]", highlight=False)
    console.print()


def print_phase_header(
    console: Console,
    num: int,
    total: int,
    title: str,
    subtitle: str = "",
) -> None:
    """Print a styled phase separator, e.g. ━━ Phase 1 of 4 · Ingestion ━━━."""
    console.print()
    console.print(
        Rule(
            f"[{BRAND}]Phase {num} of {total}[/] · [bold]{title}[/bold]",
            style=DIM,
        )
    )
    if subtitle:
        console.print(f"  [dim]{subtitle}[/dim]")
    console.print()


def print_section(console: Console, title: str, blurb: str = "") -> None:
    """Open a section: blank line, brand heading, optional dim blurb.

    The unbordered alternative to a panel, and the default for anything that is
    not a question. A border means "a discrete object you act on"; a forecast, a
    readout and a receipt are none of those, and giving all three the same
    border as the two real questions is what made the interactive path cross
    five boxes before the first phase started.
    """
    console.print()
    console.print(f"  [{BRAND}]{title}[/]")
    if blurb:
        console.print(f"  [dim]{blurb}[/dim]")


def key_value_table(
    rows: list[tuple[str, str]],
    *,
    label_width: int = 20,
    label_style: str = DIM,
    value_style: str = VALUE,
) -> Table:
    """Two-column ``label / value`` table — the shape every readout uses.

    Rich owns the gutter, which is what the hand-padded versions of this could
    not do: a label one character longer than the pad started its value a column
    right of the others, and the fix was always to widen the pad until the next
    label outgrew it too.

    The two styles are arguments because emphasis does not always fall on the
    right-hand column. A metrics row is a dim label naming a bold figure, but a
    "what's next" row is a **command** you are meant to type, described by a dim
    gloss — the same table with the emphasis the other way round. Defaulting
    both and then reusing it for next steps is how the command ends up dimmed
    and its own description ends up bold.
    """
    table = Table(box=None, padding=(0, 2), show_header=False)
    table.add_column("Label", style=label_style, min_width=label_width)
    table.add_column("Value", style=value_style)
    for label, value in rows:
        table.add_row(label, value)
    return table


def format_elapsed(seconds: float) -> str:
    """Format seconds as ``Xm Ys`` or ``X.Ys``."""
    if seconds >= 60:
        m = int(seconds) // 60
        s = int(seconds) % 60
        return f"{m}m {s}s"
    return f"{seconds:.1f}s"


def format_bytes(num_bytes: int) -> str:
    """Format a byte count as ``512 B``, ``1.5 KB``, ``1.0 MB``, etc."""
    if num_bytes < 0:
        return "—"
    if num_bytes == 0:
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    exponent = min(math.floor(math.log(num_bytes, 1024)), len(units) - 1)
    value = num_bytes / (1024**exponent)
    formatted = f"{value:.0f}" if value >= 10 or exponent == 0 else f"{value:.1f}"
    return f"{formatted} {units[exponent]}"
