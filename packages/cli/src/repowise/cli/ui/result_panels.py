"""Analysis readout, completion panel, and the notes that sit beside it.

One panel per run, and it is the last thing printed. Everything else on these
screens is a readout — the analysis interstitial, the MCP note, the list of
files init wrote into the working tree — and readouts get a section heading and
vertical rhythm rather than a border, because a border means "a discrete object
you act on" and none of them is.

The completion panel carries **two** commands. It used to carry up to seven,
assembled from every signal the run produced: the dashboard, the mode's upgrade
path, a manual MCP row, two hook installs, dead code, the top hotspot, and the
decision count. Each was individually justified and the pile was not — a list
of seven next steps does not tell you what to do next, it tells you the program
knows seven things. So the panel names the dashboard and the one move that most
needs making, and the states that are not commands moved out to the dim notes
below it, where they were always supposed to live.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from repowise.cli.ui.brand import BRAND, DIM, VALUE, key_value_table, print_section
from repowise.cli.ui.mascot import EYES_HAPPY, mini


def print_analysis_summary(
    console: Console,
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
) -> None:
    """Print the analysis-complete readout shown before generation."""
    # The headline is printed rather than passed as the blurb: ``print_section``
    # dims its blurb, and a bold count inside a dim wrapper renders bold *and*
    # dim, which is quieter than the plain bold it replaced.
    print_section(console, "Analysis complete")
    headline = (
        f"[bold]{file_count:,}[/bold] files · [bold]{symbol_count:,}[/bold] symbols"
        + (f" · [bold]{community_count}[/bold] communities" if community_count else "")
    )
    console.print(f"  {headline}")
    if lang_summary:
        console.print(f"  [dim]{lang_summary}[/dim]")
    console.print()

    rows: list[tuple[str, str]] = [
        ("Graph", f"{graph_nodes:,} nodes · {graph_edges:,} edges"),
    ]
    if git_files:
        rows.append(
            (
                "Git",
                f"{git_files:,} files indexed"
                + (f" · {hotspot_count} hotspots" if hotspot_count else ""),
            )
        )
    if dead_unreachable or dead_unused:
        rows.append(
            (
                "Dead code",
                f"{dead_unreachable} unreachable · {dead_unused} unused exports"
                + (f" · ~{dead_lines:,} lines" if dead_lines else ""),
            )
        )
    if decision_count:
        rows.append(("Decisions", f"{decision_count} extracted"))
    console.print(key_value_table(rows, label_width=11))


def build_completion_panel(
    title: str,
    metrics: list[tuple[str, str]],
    *,
    next_steps: list[tuple[str, str]] | None = None,
) -> Panel:
    """Build the run's one bordered panel, titled with the happy owl.

    *metrics* is a list of ``(label, value)`` pairs. *next_steps* is at most two
    ``(command, description)`` pairs — see :func:`build_contextual_next_steps`.
    Both columns are laid out by Rich rather than padded by hand, so a command
    longer than the gutter pushes the gutter instead of running into its own
    description.
    """
    parts: list[Any] = [key_value_table(metrics)]

    if next_steps:
        parts.append(Text(""))
        parts.append(Text("  What's next:", style="bold"))
        # Emphasis inverted against the metrics rows above: here the left column
        # is the command you type and the right is a gloss on it.
        parts.append(
            key_value_table(
                list(next_steps),
                label_width=24,
                label_style=VALUE,
                value_style=DIM,
            )
        )

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
    """The dashboard, then the single move that most needs making.

    ``repowise serve`` is the headline in every mode: the dashboard is the one
    place the graph, hotspots, dead code, decisions and wiki are all browsable,
    and it is useful before any upgrade.

    The second row is the first of these that applies, in this order, which is
    "what is stopping this index from being used at all" before "what is
    stopping it from being finished" before "what did it find":

    1. nothing wired an MCP client, so no agent can reach any of this yet;
    2. the index is deliberately partial (fast, or index-only), so completing it
       beats exploring it;
    3. otherwise the largest concrete finding, and only a generic search row
       when the run genuinely found nothing to point at.

    The MCP row goes **first**, not after the mode rows. A headless index-only
    run is the single most likely way to reach this function — it is what
    ``--no-editor-setup --index-only`` does, which is the flag pair CI uses —
    and with the mode check first, that run was told to go write prose while
    nothing on the screen said how to connect a client to the index it had just
    built. :func:`build_status_notes` stays silent for a disabled run by design,
    so this row is the only place that information exists.

    Everything that is a *state* rather than a command — a client that needs a
    restart, a hook that is not installed — is rendered by
    :func:`build_status_notes` below the panel instead.
    """
    steps: list[tuple[str, str]] = [
        ("repowise serve", "open the dashboard at http://localhost:3000"),
    ]

    if setup is not None and getattr(setup, "editor_setup_disabled", False):
        # Headless / CI / --no-editor-setup: nothing was wired, so the tools are
        # unreachable until someone connects a client by hand.
        steps.append(("repowise mcp .", "connect an MCP client (Cursor, Codex, Claude Code)"))
    elif fast_mode:
        # Fast index: graph + essential git, no docs. Point at the full result.
        steps.append(("repowise init", "upgrade to full git history + a generated wiki"))
    elif index_only:
        # `generate` is the scoped, cost-gated upgrade path — a coverage, a
        # directory or one page at a time, each behind an estimate — not the
        # all-or-nothing `update --full` this used to suggest.
        steps.append(("repowise generate", "write the subsystem pages with a model (needs a key)"))
    elif dead_unreachable + dead_unused > 0:
        steps.append(
            ("repowise dead-code", f"explore {dead_unreachable + dead_unused} dead code findings")
        )
    elif hotspot_count > 0 and top_hotspot:
        steps.append((f"repowise risk {top_hotspot}", "assess risk for the top hotspot"))
    elif decision_count > 0:
        steps.append(("repowise decision list", f"browse {decision_count} architectural decisions"))
    else:
        steps.append(('repowise search "<query>"', "search the generated wiki"))

    return steps


def build_status_notes(setup: Any) -> list[str]:
    """Rich-markup notes about MCP and hook wiring, shown beside the panel.

    Separate from the command rows because "already connected, restart it" is a
    state, not something to run — and because a note can say a thing is missing
    without implying the user must act on it now.

    A run that opted out of editor wiring entirely gets nothing: it asked for
    that, and the ``repowise mcp .`` command row already carries the one case
    where it matters.
    """
    if setup is None or getattr(setup, "editor_setup_disabled", False):
        return []

    notes: list[str] = []
    if getattr(setup, "claude_code_connected", False):
        if getattr(setup, "first_index", True):
            notes.append(
                "  [dim]Claude Code is connected to this repo. Restart it (or run "
                "[bold]/mcp[/bold]) to load the repowise tools.[/dim]"
            )
        else:
            notes.append(
                "  [dim]Claude Code stays connected; restart it only if the tools "
                "aren't showing.[/dim]"
            )
    # Cursor and Codex are not auto-wired (init writes the Claude/VS Code configs
    # and repo `.mcp.json`, not `.cursor/mcp.json`), so always point the way.
    notes.append(
        "  [dim]Cursor or Codex: run [bold]repowise mcp .[/bold] "
        "(config in [bold].repowise/mcp.json[/bold]).[/dim]"
    )

    # Hooks the interactive offers would have covered live. Surfaced only for a
    # non-interactive run, where those offers were skipped in silence. These
    # used to be command rows in the panel, which gave a thing nobody asked for
    # the same weight as the dashboard.
    if not getattr(setup, "interactive", False):
        missing = []
        if not getattr(setup, "autosync_hook_installed", False):
            missing.append("[bold]repowise hook install[/bold] keeps the index synced on commit")
        if not getattr(setup, "rewrite_hook_installed", False):
            missing.append(
                "[bold]repowise hook rewrite install[/bold] compresses noisy command output"
            )
        notes.extend(f"  [dim]{line}.[/dim]" for line in missing)

    return notes


def print_files_written(console: Console, repo_path: Path, paths: list[Path]) -> None:
    """List what init wrote into the working tree, below the panel and dim.

    Init writes editor and MCP config outside ``.repowise/`` — ``.mcp.json``,
    ``.claude/CLAUDE.md``, the two ``.vscode`` files — and until this existed it
    named none of them at the end of a run. The only trace was a green tick per
    file, printed minutes earlier and scrolled away, so the first time most
    people saw the list was in ``git status``.

    Deliberately the quietest thing on the screen: it is a receipt, not a
    result, and the reader who does not care should be able to skip it in one
    glance. It is also the reason the note names ``--no-editor-setup`` — that
    flag now genuinely writes none of this, so the sentence has somewhere to
    send anyone who does not want the files.
    """
    if not paths:
        return

    relative: list[str] = []
    for path in paths:
        try:
            relative.append(path.relative_to(repo_path).as_posix())
        except ValueError:
            relative.append(str(path))

    console.print()
    console.print("  [dim]Written to your repo (not gitignored): " + ", ".join(relative) + "[/dim]")
    console.print(
        "  [dim]These wire your editor to the index. "
        "[bold]repowise init --no-editor-setup[/bold] skips them.[/dim]"
    )
