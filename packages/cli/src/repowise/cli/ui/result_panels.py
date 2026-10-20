"""Completion panel, and the notes that sit beside it.

One panel per run, and it is the last thing printed. Everything else on these
screens is a readout — the MCP note, the list of files init wrote into the
working tree — and readouts get a section heading and vertical rhythm rather
than a border, because a border means "a discrete object you act on" and none
of them is.

The completion panel carries at most three commands: the dashboard, then the
two moves that most need making. A longer list does not tell you what to do
next, it tells you the program knows many things. States that are not commands
(a client to restart, a hook that is off) are lines, not rows.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from repowise.cli.ui import mascot
from repowise.cli.ui.brand import BRAND, DIM, VALUE, key_value_table


def build_completion_panel(
    title: str,
    metrics: list[tuple[str, str]],
    *,
    next_steps: list[tuple[str, str]] | None = None,
    body: list[Any] | None = None,
    footer: list[Any] | None = None,
    owl: str = mascot.DONE,
) -> Panel:
    """Build the run's one bordered panel, titled with the owl in state *owl*.

    ``mascot.DONE`` for a clean run, ``mascot.WARNED`` for one that finished
    degraded, ``mascot.FAILED`` for one that is about to exit non-zero.

    *metrics* is a list of ``(label, value)`` pairs. *next_steps* is at most
    three ``(command, description)`` pairs, see :func:`build_contextual_next_steps`.
    *body* renders between the metrics and the steps, *footer* after the steps.
    Both columns are laid out by Rich rather than padded by hand, so a command
    longer than the gutter pushes the gutter instead of running into its own
    description.
    """
    parts: list[Any] = [key_value_table(metrics)]
    if body:
        parts.extend(body)

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

    if footer:
        parts.append(Text(""))
        parts.extend(footer)

    return Panel(
        Group(*parts),
        title=Text.assemble(mascot.mini_text(owl), "  ", (title, "bold")),
        border_style=BRAND,
        padding=(1, 1),
    )


def print_completion_panel(
    console: Console,
    title: str,
    metrics: list[tuple[str, str]],
    next_steps: list[tuple[str, str]] | None = None,
    *,
    body: list[Any] | None = None,
    footer: list[Any] | None = None,
    owl: str = mascot.DONE,
) -> None:
    """The completion panel on a terminal; off one, the same rows as plain lines.

    A border in a log or an agent's transcript is only padding to strip.
    """
    if console.is_terminal:
        console.print(
            build_completion_panel(
                title, metrics, next_steps=next_steps, body=body, footer=footer, owl=owl
            )
        )
        return
    console.print(Text.assemble(mascot.mini_text(owl), "  ", title), highlight=False)
    console.print(key_value_table(metrics))
    for line in body or []:
        console.print(line)
    if next_steps:
        console.print("  What's next:")
        console.print(key_value_table(list(next_steps), label_width=24))
    for line in footer or []:
        console.print(line)


def serve_step(target: str = "dashboard") -> tuple[str, str]:
    """The ``repowise serve`` row every completion panel leads with.

    The port is the one serve tries first; serve moves to the next free port
    when it is taken, so the row says so rather than promising an exact URL.
    """
    from repowise.cli.commands.serve_cmd._ports import DEFAULT_UI_PORT
    from repowise.cli.commands.serve_cmd._web_assets import _NODE_MIN_MAJOR

    return (
        "repowise serve",
        f"{target} at http://localhost:{DEFAULT_UI_PORT} or the next free port\n"
        f"(needs Node {_NODE_MIN_MAJOR}+; the first run downloads the UI)",
    )


#: Rows after ``repowise serve``.
_MAX_FOLLOW_UPS = 2


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
    """The dashboard, then the two moves that most need making.

    ``repowise serve`` is the headline in every mode: the dashboard is the one
    place the graph, hotspots, dead code, decisions and documentation are all
    browsable, and it is useful before any upgrade.

    The follow-ups are the first two of these that apply, in this order, which
    is "what is stopping this index from being used at all" before "what is
    stopping it from being finished" before "what did it find":

    1. nothing wired an MCP client, so no agent can reach any of this yet
       (``--no-editor-setup``, the CI shape, has no Connected line to say so);
    2. the index is deliberately partial (fast, or index-only);
    3. the largest concrete finding, or a search row when there is none.
    """
    candidates: list[tuple[str, str]] = []
    if setup is not None and getattr(setup, "editor_setup_disabled", False):
        candidates.append(("repowise mcp .", "connect an MCP client (Cursor, Codex, Claude Code)"))
    if fast_mode:
        from repowise.core.index_scope import FAST_TO_FULL_COMMAND

        candidates.append(
            (FAST_TO_FULL_COMMAND, "build documentation from code and the full git history (no key)")
        )
    elif index_only:
        # `generate` is the scoped, cost-gated upgrade: a coverage, a directory
        # or one page at a time, each behind an estimate.
        candidates.append(
            ("repowise generate", "write the documentation with a model (needs a key)")
        )
    dead = dead_unreachable + dead_unused
    if dead > 0:
        noun = "finding" if dead == 1 else "findings"
        candidates.append(("repowise dead-code", f"explore {dead} dead code {noun}"))
    elif hotspot_count > 0 and top_hotspot:
        candidates.append(
            (f"repowise risk {escape(top_hotspot)}", "assess risk for the top hotspot")
        )
    elif decision_count > 0:
        candidates.append(
            ("repowise decision list", f"browse {decision_count} architectural decisions")
        )
    elif not fast_mode:
        candidates.append(('repowise search "<query>"', "search the documentation"))

    return [serve_step(), *candidates[:_MAX_FOLLOW_UPS]]


def keyless_upgrade_note(semantic_available: bool, reindex_command: str | None = None) -> str:
    """One sentence naming the upgrade commands a keyless index has left.

    Shared verbatim between ``repowise init``'s closing note and ``repowise
    status``'s template-only scope line (#3105), so the wording cannot drift
    between the two surfaces. Plain wording, no counts or percentages, and
    nothing implying the keyless index is unfinished or broken — it is a
    complete index; this just names what more is available.

    ``reindex_command`` lets a caller that has ``search.next_command`` from
    the stamped scope use that exact command instead of the generic default,
    so the note cannot name a flag combination the run did not actually need.
    """
    prose = (
        "repowise generate writes the subsystem pages with a model "
        "(needs a key, or --provider ollama for a local model)"
    )
    if semantic_available:
        return f"{prose}."
    command = reindex_command or "repowise reindex"
    return f"{prose}; {command} adds semantic search."
