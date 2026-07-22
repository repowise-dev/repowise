"""Analysis-summary, completion, and next-step panels."""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from repowise.cli.ui.brand import BRAND
from repowise.cli.ui.mascot import EYES_HAPPY, mini


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


def _render_what_next_lines(next_steps: list[tuple[str, str]]) -> list[str]:
    """Format the ``What's next:`` rows for the completion panel.

    Pads short commands to a 28-column gutter for alignment, but always
    inserts at least one space between command and description so a long
    command like ``repowise init --provider gemini`` (>28 chars) doesn't
    run straight into its description text.
    """
    return [f"  {cmd:<28} {desc}" for cmd, desc in next_steps]


def build_completion_panel(
    title: str,
    metrics: list[tuple[str, str]],
    *,
    next_steps: list[tuple[str, str]] | None = None,
) -> Panel:
    """Build a bordered summary panel, titled with the happy owl.

    *metrics* is a list of ``(label, value)`` pairs.
    *next_steps* is an optional list of ``(command, description)`` pairs.
    The mascot prefix lives here (not at call sites) so every completion
    panel gets it consistently.
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
        for line in _render_what_next_lines(next_steps):
            parts.append(Text(line, style="dim"))

    return Panel(
        Group(*parts),
        title=f"[bold]{mini(EYES_HAPPY)}  {title}[/bold]",
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
    setup: Any = None,
) -> list[tuple[str, str]]:
    """Build next-step suggestions from what the run actually did.

    ``repowise serve`` is the headline in every mode: the dashboard is the one
    place the graph, hotspots, dead code, decisions and wiki are all browsable.
    The second row is the mode's natural next move (upgrade for fast/index-only,
    search for a written wiki).

    *setup* is an optional :class:`~repowise.cli.editor_setup.EditorSetupOutcome`.
    When present, the panel reacts to it: a headless run gets a manual MCP row,
    and a **non-interactive** run whose hooks are missing gets install rows
    (an interactive run was already offered them live, so re-listing would nag).
    The Claude Code "already connected, restart it" status is a note rendered
    beside the panel, not a command row — see :func:`build_mcp_status_lines`.
    """
    steps: list[tuple[str, str]] = []

    # Headline: the dashboard, always. Graph, hotspots and dead code are there
    # even in fast/index-only mode, so it is useful before any upgrade.
    steps.append(("repowise serve", "open the dashboard at http://localhost:3000"))

    if fast_mode:
        # Fast index: graph + essential git, no docs. Point at the full result.
        steps.append(("repowise init", "upgrade to full git history + a generated wiki"))
    elif index_only:
        # `generate` is the scoped, cost-gated upgrade path — a coverage, a
        # directory or one page at a time, each behind an estimate — not the
        # all-or-nothing `update --full` this used to suggest.
        steps.append(("repowise generate", "upgrade the wiki to model-written prose (needs a key)"))
    else:
        steps.append(('repowise search "<query>"', "search the generated wiki"))

    # MCP: only a manual row when nothing was auto-wired (headless/CI). When a
    # client was registered, the "restart to load the tools" status goes in the
    # note beside the panel instead of as a command.
    if setup is not None and getattr(setup, "editor_setup_disabled", False):
        steps.append(("repowise mcp .", "connect an MCP client (Cursor, Codex, Claude Code)"))

    # Hooks the interactive offers would have covered live. Surface them here
    # only for a non-interactive run (where the offers were skipped silently)
    # and only when the hook is actually missing. A headless / skip-setup run
    # opted out of all editor wiring, so it is never nagged to install hooks.
    if (
        setup is not None
        and not getattr(setup, "interactive", False)
        and not getattr(setup, "editor_setup_disabled", False)
    ):
        if not getattr(setup, "autosync_hook_installed", False):
            steps.append(("repowise hook install", "auto-sync the index on every commit"))
        if not getattr(setup, "rewrite_hook_installed", False):
            steps.append(
                ("repowise hook rewrite install", "compress noisy command output via distill")
            )

    if dead_unreachable + dead_unused > 0:
        steps.append(
            ("repowise dead-code", f"explore {dead_unreachable + dead_unused} dead code findings")
        )

    if hotspot_count > 0 and top_hotspot:
        steps.append((f"repowise risk {top_hotspot}", "assess risk for top hotspot"))

    if decision_count > 0:
        steps.append(("repowise decisions", f"browse {decision_count} architectural decisions"))

    return steps


def build_mcp_status_lines(setup: Any) -> list[str]:
    """Rich-markup status lines about MCP wiring, shown beside the panel.

    Separate from the command rows because "already connected, restart it" is a
    state, not something to run. Returns an empty list for a headless run (the
    manual ``repowise mcp .`` command row carries that case instead).
    """
    if setup is None or getattr(setup, "editor_setup_disabled", False):
        return []

    lines: list[str] = []
    if getattr(setup, "claude_code_connected", False):
        if getattr(setup, "first_index", True):
            lines.append(
                "  [dim]Claude Code is connected to this repo. Restart it (or run "
                "[bold]/mcp[/bold]) to load the repowise tools.[/dim]"
            )
        else:
            lines.append(
                "  [dim]Claude Code stays connected; restart it only if the tools "
                "aren't showing.[/dim]"
            )
    # Cursor and Codex are not auto-wired (init writes the Claude/VS Code configs
    # and repo `.mcp.json`, not `.cursor/mcp.json`), so always point the way.
    lines.append(
        "  [dim]Cursor or Codex: run [bold]repowise mcp .[/bold] "
        "(config in [bold].repowise/mcp.json[/bold]).[/dim]"
    )
    return lines
