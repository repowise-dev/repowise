"""Brand / theme constants and basic banner + phase-header rendering."""

from __future__ import annotations

from rich.console import Console
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
# ASCII art  —  owl mascot + heatmap wordmark (see ui/mascot.py)
# ---------------------------------------------------------------------------

# Plain-text compact render, kept as cheap insurance for anything that ever
# reached for the private ``_LOGO`` name. The real banner is composed (and
# coloured) by ``mascot.banner_text``.
_LOGO = (
    " ,___,  ███▄  ███ ███▄ ▄██▄ █   █ ███ ▄██▄ ███\n"
    " (◉,◉)  █  █  █   █  █ █  █ █   █  █  █    █\n"
    " ( ▼ )  ███▀  ██  ███▀ █  █ █ █ █  █  ▀██▄ ██\n"
    " /)_(\\  █ ▀▄  █   █    █  █ █ █ █  █     █ █\n"
    '  " "   █  ▀▄ ███ █    ▀██▀ ▀█▀█▀ ███ ▀██▀ ███'
)

# Full banner is 78 cols; below this width fall back to the compact variant
# (same design at 1-char strokes, 47 cols).
_FULL_BANNER_MIN_WIDTH = 82


def print_banner(console: Console, repo_name: str | None = None) -> None:
    """Print the repowise owl banner, tagline, and optional repo name."""
    from repowise.cli import __version__
    from repowise.cli.ui import mascot

    compact = console.width < _FULL_BANNER_MIN_WIDTH
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
        console.print(f" Repository: [bold]{repo_name}[/bold]", highlight=False)
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
