"""``repowise next`` - the short list of things worth doing, from the index.

Reads the same stored actions the web app's "Do next" and ``get_overview``'s
``next_actions`` rank, so the terminal, the agent and the UI agree.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import click

from repowise.cli.helpers import console, repo_index_session, resolve_command_target, run_async
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

TIER_HEADING = {
    "act_now": "Now",
    "plan": "Worth planning",
    "improve_signal": "Improve what Repowise can see",
}

_TIER_COLOUR = {"act_now": "red", "plan": "yellow", "improve_signal": "cyan"}


async def _read_view(root: Path) -> dict[str, Any] | None:
    """The actions view for *root*, or ``None`` when there is no index to read."""
    from repowise.core.persistence.crud.analysis.actions import load_actions_view

    async with repo_index_session(root) as opened:
        if opened is None:
            return None
        session, repo_id = opened
        return await load_actions_view(session, repo_id)


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


def work(view: dict[str, Any], horizon: str) -> tuple[int, int]:
    """``(act_now + plan, act_now)`` for *horizon*: the part that is real work."""
    by_tier = view["horizons"][horizon].get("by_tier", {})
    now = int(by_tier.get("act_now", 0))
    return now + int(by_tier.get("plan", 0)), now


def default_horizon(view: dict[str, Any]) -> str:
    """The week, unless it holds no work and the quarter does (as the UI opens)."""
    if work(view, "week")[0] == 0 and work(view, "quarter")[0] > 0:
        return "quarter"
    return "week"


def _plural(n: int, noun: str) -> str:
    return f"{n:,} {noun}{'' if n == 1 else 's'}"


def _day(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return f"{d:%b} {d.day}"


def status_sentence(view: dict[str, Any], horizon: str) -> str:
    """Where things stand, in one sentence. Mirrors the web app's ``actionsStatus``.

    It names the window because "this week" means the repository's last week
    of commits, which is not always the calendar's.
    """
    total, now = work(view, horizon)
    until = _day(view.get("anchor"))
    if horizon == "week":
        window = f"in the week to {until}, the last indexed commit" if until else "this week"
    else:
        window = "this quarter"
    if total == 0:
        other = "quarter" if horizon == "week" else "week"
        other_total = work(view, other)[0]
        if other_total == 0:
            return f"Nothing stands out {window}."
        if horizon == "week":
            verb = "is" if other_total == 1 else "are"
            tail = f"{verb} worth planning this quarter"
        else:
            tail = "came up this week"
        return f"Nothing needs you {window}. {_plural(other_total, 'thing')} {tail}."
    now_part = f", {now} of them now" if now else ""
    return f"{_plural(total, 'thing')} worth doing {window}{now_part}."


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

    h = view["horizons"][horizon]
    console.print(f"[bold]{escape(status_sentence(view, horizon))}[/bold]")
    shown = h["actions"][:limit]
    for tier, heading in TIER_HEADING.items():
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
        console.print(f"[dim]{_plural(h['hidden'], 'action')} dismissed, snoozed or done.[/dim]")
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
@format_option(help="Output format. json prints the whole stored view.")
@json_option(help="Alias for --format json.")
def next_command(
    path: str | None,
    horizon: str | None,
    show_all: bool,
    repo_alias: str | None,
    no_workspace: bool,
    fmt: str,
    as_json: bool,
) -> None:
    """Show the few things worth doing next, ranked from the index."""
    fmt = resolve_format(fmt, as_json)
    target = resolve_command_target(
        path=path, no_workspace_flag=no_workspace, repo_alias=repo_alias
    )
    target.notice(notice_console(fmt), command="next")
    root = target.single_repo_path().resolve()
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
