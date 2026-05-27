"""Analysis-summary, completion, and next-step panels."""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from repowise.cli.ui.brand import BRAND


def build_analysis_summary_panel(
    *,
    file_count: int,
    symbol_count: int,
    graph_nodes: int,
    graph_edges: int,
    dead_unreachable: int = 0,
    dead_unused: int = 0,
    dead_lines: int = 0,
    decision_count: int = 0,
    git_files: int = 0,
    hotspot_count: int = 0,
    community_count: int = 0,
    lang_summary: str = "",
) -> Panel:
    """Compact analysis-complete interstitial shown before generation."""
    lines: list[str] = []
    lines.append(
        f"  [bold]{file_count:,}[/bold] files · "
        f"[bold]{symbol_count:,}[/bold] symbols"
        + (f" · [bold]{community_count}[/bold] communities" if community_count else "")
    )
    if lang_summary:
        lines.append(f"  [dim]{lang_summary}[/dim]")
    lines.append("")
    lines.append(
        f"  Graph    [bold]{graph_nodes:,}[/bold] nodes · [bold]{graph_edges:,}[/bold] edges"
    )
    if git_files:
        lines.append(
            f"  Git      [bold]{git_files:,}[/bold] files indexed"
            + (f" · [bold]{hotspot_count}[/bold] hotspots" if hotspot_count else "")
        )
    if dead_unreachable or dead_unused:
        lines.append(
            f"  Dead     [bold]{dead_unreachable}[/bold] unreachable · "
            f"[bold]{dead_unused}[/bold] unused exports"
            + (f" · ~{dead_lines:,} lines" if dead_lines else "")
        )
    if decision_count:
        lines.append(f"  Decisions [bold]{decision_count}[/bold] extracted")

    return Panel(
        "\n".join(lines),
        title="[bold]Analysis Complete[/bold]",
        border_style=BRAND,
        padding=(1, 1),
    )


def build_completion_panel(
    title: str,
    metrics: list[tuple[str, str]],
    *,
    next_steps: list[tuple[str, str]] | None = None,
) -> Panel:
    """Build a bordered summary panel.

    *metrics* is a list of ``(label, value)`` pairs.
    *next_steps* is an optional list of ``(command, description)`` pairs.
    """
    table = Table(box=None, padding=(0, 2), show_header=False)
    table.add_column("Metric", style="dim", min_width=20)
    table.add_column("Value", style="bold")

    for label, value in metrics:
        table.add_row(label, value)

    parts: list[Any] = [table]

    if next_steps:
        parts.append(Text(""))
        parts.append(Text("  What's next:", style="bold"))
        for cmd, desc in next_steps:
            parts.append(Text(f"  {cmd:<28}{desc}", style="dim"))

    return Panel(
        Group(*parts),
        title=f"[bold]{title}[/bold]",
        border_style=BRAND,
        padding=(1, 1),
    )


def build_contextual_next_steps(
    *,
    index_only: bool,
    fast_mode: bool = False,
    dead_unreachable: int = 0,
    dead_unused: int = 0,
    hotspot_count: int = 0,
    decision_count: int = 0,
    top_hotspot: str = "",
) -> list[tuple[str, str]]:
    """Build next-step suggestions based on what the analysis actually found.

    When *fast_mode* is set, the index used the essential git tier and skipped
    LLM docs, so the suggestions lead with how to upgrade to the full result.
    """
    steps: list[tuple[str, str]] = []

    if fast_mode:
        # Fast index: graph + essential git, no docs. Tell them how to get the
        # full thing — complete git history + generated wiki pages.
        steps.append(("repowise init", "run full mode: complete git history + generate docs"))
        steps.append(("repowise mcp .", "start MCP server for AI assistants now"))
    elif index_only:
        steps.append(("repowise mcp .", "start MCP server for AI assistants"))
        steps.append(("repowise init --provider gemini", "generate full documentation"))
    else:
        steps.append(("repowise mcp .", "start MCP server for AI assistants"))
        steps.append(("repowise search <query>", "search the generated wiki"))

    if dead_unreachable + dead_unused > 0:
        steps.append(
            ("repowise dead-code", f"explore {dead_unreachable + dead_unused} dead code findings")
        )

    if hotspot_count > 0 and top_hotspot:
        steps.append((f"repowise risk {top_hotspot}", "assess risk for top hotspot"))

    if decision_count > 0:
        steps.append(("repowise decisions", f"browse {decision_count} architectural decisions"))

    if not steps:
        steps.append(("repowise mcp .", "start MCP server for AI assistants"))
        steps.append(("repowise search <query>", "search the index"))

    return steps
