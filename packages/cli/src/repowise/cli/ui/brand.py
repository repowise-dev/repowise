"""Brand / theme constants and basic banner + phase-header rendering."""

from __future__ import annotations

from rich.console import Console
from rich.rule import Rule
from rich.text import Text

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
# ASCII art  —  bold half-block, compact lowercase, 2 lines
# ---------------------------------------------------------------------------

_LOGO = " █▀█ █▀▀ █▀█ █▀█ █ █ █ ▀ █▀▀ █▀▀\n █▀▄ ██▄ █▀▀ █▄█ ▀▄▀▄▀ █ ▄▄█ ██▄"


def print_banner(console: Console, repo_name: str | None = None) -> None:
    """Print the repowise logo, tagline, and optional repo name."""
    from repowise.cli import __version__

    console.print()
    console.print(Text(_LOGO, style=BRAND_STYLE))
    console.print(
        f"  [dim]codebase intelligence for developers and AI[/dim]  [dim]v{__version__}[/dim]"
    )
    if repo_name:
        console.print()
        console.print(f"  Repository: [bold]{repo_name}[/bold]")
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
