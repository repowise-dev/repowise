"""Brand / theme constants and basic banner + phase-header rendering."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape
from rich.rule import Rule

# ---------------------------------------------------------------------------
# Brand / theme
# ---------------------------------------------------------------------------

BRAND = "#F59520"
BRAND_STYLE = f"bold {BRAND}"
DIM = "dim"
OK = "green"
WARN = "yellow"
ERR = "bold red"

# ---------------------------------------------------------------------------
# Banner  —  owl mascot + heatmap wordmark (art lives in ui/mascot.py)
# ---------------------------------------------------------------------------

# Breathing room required beyond the rendered banner width before we pick the
# full variant; below that we fall back to compact (same design, 1-char strokes).
_BANNER_WIDTH_MARGIN = 4


def print_banner(console: Console, repo_name: str | None = None) -> None:
    """Print the repowise owl banner, tagline, and optional repo name."""
    from repowise.cli import __version__
    from repowise.cli.ui import mascot

    compact = console.width < mascot.banner_width() + _BANNER_WIDTH_MARGIN
    console.print()
    console.print(mascot.banner_text(repo_name, compact=compact))
    console.print()
    if compact:
        console.print(f" [dim]codebase intelligence · v{__version__}[/dim]", highlight=False)
    else:
        console.print(
            f" [dim]codebase intelligence for developers and AI  ·  v{__version__}[/dim]",
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


def format_elapsed(seconds: float) -> str:
    """Format seconds as ``Xm Ys`` or ``X.Ys``."""
    if seconds >= 60:
        m = int(seconds) // 60
        s = int(seconds) % 60
        return f"{m}m {s}s"
    return f"{seconds:.1f}s"
