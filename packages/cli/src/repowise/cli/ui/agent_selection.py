"""The one prompt that asks which agents repowise should wire up.

Replaces three sequential yes/no questions — one per agent, asked in a fixed
order, each written by hand — with a single checklist whose boxes are ticked
from detection. The three questions had a wart the checklist removes rather
than explains: Codex defaulted to *no* while its two neighbours defaulted to
yes, so Enter-through silently inverted in the middle, and the code had to
apologise for it in a dim line. A box that is ticked because the agent is
installed needs no apology.

Shared by ``init`` and ``repowise agents add``, which is what keeps their
answers to "is this answerable" identical rather than merely similar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.prompt import Prompt

from repowise.cli.ui.brand import BRAND_STYLE
from repowise.cli.ui.workspace_selection import parse_selection


@dataclass(frozen=True)
class AgentChoice:
    """One row of the checklist."""

    id: str
    display_name: str
    #: Why the box is (or is not) ticked — "wired already", "installed", "".
    detail: str
    #: Whether it starts ticked.
    enabled: bool


def interactive_agent_select(
    console_obj: Any,
    choices: list[AgentChoice],
) -> set[str] | None:
    """Ask which agents to set up. Returns the chosen ids, or None.

    ``None`` means the question could not be asked — stdin reported a terminal
    and then returned EOF. That happens under Git Bash on Windows with
    ``< /dev/null``, under some pty wrappers, and under ``docker run -t``
    without ``-i``; agents drive the CLI through exactly those shapes. The
    caller treats None as "keep the pre-ticked set" and carries on, rather than
    dying on a question nobody can hear. Only ``EOFError`` is swallowed: a real
    Ctrl-C still raises and still stops the run.

    Enter accepts the ticked set. Numbers *toggle*, so unticking the one box
    the user disagrees with costs one keystroke rather than retyping the rest.
    """
    if not choices:
        return set()

    selected = {choice.id for choice in choices if choice.enabled}

    console_obj.print()
    # Says "project files" rather than "set up" because that is the honest
    # scope: it is what the three per-agent prompts it replaced controlled.
    # The global MCP registration is a separate step and unticking a box here
    # does not withdraw it, so the question must not imply that it does.
    console_obj.print(
        "[bold]Agent integrations:[/bold] write project config and instruction files for?"
    )
    for index, choice in enumerate(choices, 1):
        # ``\[`` escapes the bracket for rich. Unescaped, ``[x]`` is valid
        # markup and gets eaten as a style tag while ``[ ]`` is not and
        # survives — so every ticked box rendered blank and every unticked one
        # rendered as a box, which reads as the exact inverse of the truth.
        box = r"\[x]" if choice.id in selected else r"\[ ]"
        detail = f"  [dim]{choice.detail}[/dim]" if choice.detail else ""
        console_obj.print(
            f"  {box} [{BRAND_STYLE}][{index}][/] {choice.display_name}{detail}"
        )
    console_obj.print("  [dim]Enter to accept, or numbers to toggle (1,3), 'all', 'none'.[/dim]")

    try:
        raw = Prompt.ask("  Set up", default="", show_default=False, console=console_obj)
    except EOFError:
        return None

    raw = raw.strip().lower()
    if not raw:
        return selected
    if raw == "all":
        return {choice.id for choice in choices}
    if raw == "none":
        return set()

    toggled = parse_selection(raw, len(choices))
    if toggled is None:
        # One reprompt would be a loop with no exit for a non-interactive
        # caller that answered garbage. The ticked set is a safe answer and the
        # user can rerun `repowise agents add` to change it.
        console_obj.print("  [dim]Not a selection; keeping the ticked agents.[/dim]")
        return selected

    for index in toggled:
        choice_id = choices[index].id
        selected.symmetric_difference_update({choice_id})
    return selected
