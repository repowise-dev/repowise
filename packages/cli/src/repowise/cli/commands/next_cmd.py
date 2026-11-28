"""``repowise next`` - the short list of things worth doing, from the index.

Reads the same stored actions the web app's "Do next" and ``get_overview``'s
``next_actions`` rank, so the terminal, the agent and the UI agree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from repowise.cli.helpers import (
    console,
    cross_repo_cut_inputs,
    repo_index_session,
    resolve_command_target,
    run_async,
)
from repowise.cli.output import (
    emit_json,
    format_option,
    json_option,
    notice_console,
    resolve_format,
)

#: Rows shown by default and with ``--all``. The stored view keeps 20 a horizon.
PREVIEW = 5
ALL = 20

_TIER_COLOUR = {"act_now": "red", "plan": "yellow", "improve_signal": "cyan"}


#: The one prompt that hands every setup step to the agent.
PROMPT_LINE = "repowise next --prompt setup"


def _rewrite_hook() -> bool | None:
    """Whether Claude Code's distill rewrite hook is installed; ``None`` when unreadable."""
    try:
        from repowise.cli.editor_integrations.claude_config import (
            claude_code_rewrite_hook_installed,
        )

        return claude_code_rewrite_hook_installed()
    except Exception:
        return None


async def _read_view(root: Path) -> dict[str, Any] | None:
    """The actions view for *root* with the live setup steps, or ``None`` with no index."""
    from repowise.cli._helpers.paths import find_workspace_root
    from repowise.core.persistence.crud.analysis.actions import load_actions_view
    from repowise.core.persistence.crud.analysis.setup_facts import load_setup_facts

    async with repo_index_session(root) as opened:
        if opened is None:
            return None
        session, repo_id = opened
        cut = cross_repo_cut_inputs(root, find_workspace_root(root))
        setup = await load_setup_facts(session, repo_id, root, rewrite_hook=_rewrite_hook())
        return await load_actions_view(session, repo_id, setup=setup, **cut)


async def _dismiss_setup(root: Path) -> bool:
    """Record the declined pass in the index; ``False`` with no index to write."""
    from repowise.core.persistence.crud.analysis.setup_facts import dismiss_setup
    from repowise.core.setup_state import write_setup_state

    async with repo_index_session(root) as opened:
        if opened is None:
            return False
        session, repo_id = opened
        await dismiss_setup(session, repo_id)
        await session.commit()
    write_setup_state(root, dismissed=True, offer=None)
    return True


def load_view(root: Path) -> dict[str, Any] | None:
    """:func:`_read_view`, and ``None`` on any failure.

    The loader already reports a store missing from an older index as
    ``unavailable``; this guard is for everything else, so ``status`` can call
    it without risking its own output.
    """
    try:
        return run_async(_read_view(root))
    except Exception:
        return None


def default_horizon(view: dict[str, Any]) -> str:
    """The week, unless it holds no work and the quarter does (as the UI opens)."""
    from repowise.core.analysis.actions.summary import work

    week, quarter = (work(view["horizons"][h]) for h in ("week", "quarter"))
    return "quarter" if week == 0 and quarter > 0 else "week"


def sentence(view: dict[str, Any], horizon: str) -> str:
    """The view's own sentence; worded by core here for a view that predates it."""
    found = (view.get("summary") or {}).get(horizon)
    if found:
        return found
    from datetime import datetime

    from repowise.core.analysis.actions.summary import summarize

    anchor = view.get("anchor")
    return summarize(view["horizons"], datetime.fromisoformat(anchor) if anchor else None)[horizon]


def render_title(title: str) -> str:
    """Rich markup for action text: the backtick spans (paths, symbols) set bold cyan."""
    from rich.markup import escape

    parts = title.split("`")
    return "".join(
        f"[bold cyan]{escape(p)}[/bold cyan]" if i % 2 else escape(p) for i, p in enumerate(parts)
    )


def _render_action(action: dict[str, Any]) -> None:
    from rich.markup import escape

    console.print(f"  [bold]-[/bold] {render_title(action['title'])}")
    if action.get("impact"):
        console.print(f"    [dim]{render_title(action['impact'])}[/dim]")
    why = [f"{w['label']}: {w['value']}" for w in action.get("why") or []]
    if why:
        console.print(f"    {escape(' · '.join(why))}")
    if action.get("done_when"):
        console.print(f"    [dim]Done when: {render_title(action['done_when'])}[/dim]")
    if action.get("command"):
        console.print(f"    [green]$ {escape(action['command'])}[/green]")


def render(view: dict[str, Any], horizon: str, limit: int) -> None:
    from rich.markup import escape

    from repowise.core.analysis.actions import TIER_LABELS
    from repowise.core.analysis.actions.rules._text import plural

    h = view["horizons"][horizon]
    console.print(f"[bold]{escape(sentence(view, horizon))}[/bold]")
    shown = h["actions"][:limit]
    for tier, heading in TIER_LABELS.items():
        rows = [a for a in shown if a["tier"] == tier]
        if not rows:
            continue
        colour = _TIER_COLOUR[tier]
        console.print()
        console.print(f"[{colour}]{heading}[/{colour}]")
        for action in rows:
            _render_action(action)

    console.print()
    total = h.get("total", len(h["actions"]))
    if total > len(shown):
        more = "" if limit >= ALL else " [bold]--all[/bold] shows up to 20."
        console.print(f"[dim]Showing {len(shown)} of {total}.{more}[/dim]")
    if h.get("hidden"):
        console.print(f"[dim]{plural(h['hidden'], 'action')} dismissed, snoozed or done.[/dim]")
    setup = view.get("setup") or {}
    quiet = setup.get("dismissed") or setup.get("completed")
    if any(a["tier"] == "improve_signal" for a in shown) and not quiet:
        console.print(
            f"[dim]Or hand all of it to your agent: [bold]{PROMPT_LINE}[/bold] prints one prompt.[/dim]"
        )
    unavailable = sorted(view.get("unavailable") or {})
    if unavailable:
        console.print(
            f"[dim]Not in this index yet: {escape(', '.join(unavailable))}. "
            "Run [bold]repowise update[/bold] to add them.[/dim]"
        )


@click.command("next")
@click.argument("path", required=False, default=None)
@click.option(
    "--horizon",
    type=click.Choice(["week", "quarter"]),
    default=None,
    help="Window to rank. Default: the week, or the quarter when the week holds no work.",
)
@click.option("--all", "show_all", is_flag=True, default=False, help="Show up to 20, not 5.")
@click.option("--repo", "repo_alias", default=None, help="In workspace mode, target one repo.")
@click.option("--no-workspace", is_flag=True, default=False, help="Force single-repo mode.")
@click.option(
    "--prompt",
    "prompt",
    type=click.Choice(["setup"]),
    default=None,
    help="Print the prompt that hands every setup step to your agent.",
)
@click.option(
    "--dismiss",
    "dismiss",
    type=click.Choice(["setup"]),
    default=None,
    help="Stop offering the setup pass here. The steps stay in this list.",
)
@format_option(help="Output format. json prints the whole stored view.")
@json_option(help="Alias for --format json.")
def next_command(
    path: str | None,
    horizon: str | None,
    show_all: bool,
    repo_alias: str | None,
    no_workspace: bool,
    prompt: str | None,
    dismiss: str | None,
    fmt: str,
    as_json: bool,
) -> None:
    """Show the few things worth doing next, ranked from the index."""
    fmt = resolve_format(fmt, as_json)
    if prompt == "setup":
        from repowise.core.agent_prompts import render_finish_setup

        # Plain print: the text is pasted or piped, so no markup and no wrapping.
        click.echo(render_finish_setup())
        return
    target = resolve_command_target(
        path=path, no_workspace_flag=no_workspace, repo_alias=repo_alias
    )
    root = target.narrow(notice_console(fmt), command="next").resolve()
    if dismiss == "setup":
        done = run_async(_dismiss_setup(root))
        if fmt == "json":
            emit_json({"repo": str(root), "setup": {"dismissed": done}})
        elif done:
            console.print(
                "Repowise will stop offering the setup pass here. "
                "The steps stay in [bold]repowise next[/bold]."
            )
        else:
            console.print("[yellow]No readable index here. Run 'repowise init'.[/yellow]")
        return
    view = load_view(root)

    if fmt == "json":
        emit_json(view if view is not None else {"repo": str(root), "status": "unavailable"})
        return
    if view is None:
        console.print(
            "[yellow]No readable index here. Run 'repowise init' (or 'repowise update').[/yellow]"
        )
        return
    render(view, horizon or default_horizon(view), ALL if show_all else PREVIEW)
