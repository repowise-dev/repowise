"""``repowise agents`` — wire repowise into the agents on this machine.

A noun group, following ``repowise hook``, rather than a top-level verb that
would compete with ``init`` for meaning. ``init`` is still the front door and
still sets agents up as part of a first run; this group is for the cases that
come after it — adding an agent you installed later, removing one, refreshing
a config after an upgrade, or printing a snippet for a host repowise does not
write files for at all.

Two contracts worth reading before editing:

**``--format json`` is on every subcommand, including the ones that write.**
``doctor`` made json incompatible with ``--repair``, which is right for a
diagnostic and wrong here: the whole point of a wiring command is that an agent
can run it and read back what changed. ``WriteResult``'s action enum is already
that payload, so there is nothing to invent.

**One payload, two renderers.** Every subcommand builds a dict and then either
prints a table *from that dict* or emits it. Never a second projection assembled
for the table alone. A trimmed projection has two silent failure modes — a key
dropped, and a key kept that nothing prints — and only the first is visible to a
test that checks the projection by itself.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click

from repowise.cli.agent_targets.registry import default_selection, describe_agents
from repowise.cli.helpers import console, resolve_command_target
from repowise.cli.output import emit_json, format_option, notice_console

#: ``--target`` accepts these alongside a comma-separated list of ids.
_TARGET_KEYWORDS = "auto, all, none"


# ---------------------------------------------------------------------------
# Shared resolution
# ---------------------------------------------------------------------------


def _repo_path(path: str | None, fmt: str) -> Path:
    """The repo these subcommands act on.

    Single-repo only, deliberately. ``agents add --workspace`` would have to
    decide whether a user-scope MCP registration means the workspace root or
    each member repo, and there is exactly one global ``repowise`` entry to
    point somewhere. Workspace support is worth having when someone asks for it
    with a concrete answer to that question.
    """
    target = resolve_command_target(path=path, workspace_flag=False, no_workspace_flag=True)
    target.notice(notice_console(fmt), command="agents")
    assert target.repo_path is not None
    return target.repo_path


def _scopes_for(target: Any, scope: str) -> list[Any]:
    """Which scopes to act on for *target*, honouring what it supports."""
    from repowise.cli.agent_targets.types import Scope

    wanted = [Scope.PROJECT, Scope.USER] if scope == "both" else [Scope(scope)]
    return [s for s in wanted if target.supports_scope(s)]


def _resolve_targets(target_flag: str, repo_path: Path) -> list[Any]:
    from repowise.cli.agent_targets.registry import resolve_target_flag

    try:
        return resolve_target_flag(target_flag, repo_path)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc


def _can_prompt(target_flag: str | None, yes: bool) -> bool:
    """The interactive gate: a TTY **and** no ``--target``, never a TTY alone.

    An explicit ``--target`` is already an answer to the question the prompt
    asks, so asking again is at best noise and at worst a block. And an agent
    driving this under a pty reports a TTY it cannot answer from, which is why
    ``isatty`` is the last condition rather than the only one — the prompt
    itself treats EOF as "not interactive" and falls through to the defaults.
    """
    if target_flag is not None or yes:
        return False
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Payload construction — the single source both renderers read
# ---------------------------------------------------------------------------


def _write_payload(
    action: str,
    repo_path: Path,
    targets: list[Any],
    scope: str,
    *,
    remove: bool,
) -> dict:
    """Run the installs (or uninstalls) and return the record of what happened.

    Built here, at the site that holds the ``WriteResult``, rather than
    re-derived by a renderer. ``skipped`` carries the one case where nothing was
    written on purpose: a host-managed method already in place, which is the
    duplicate-registration stand-down.
    """
    from repowise.cli.agent_targets.registry import select_install_method
    from repowise.cli.agent_targets.types import Scope
    from repowise.cli.editor_setup import is_editor_setup_disabled

    user_scope_disabled = is_editor_setup_disabled()

    agents: list[dict] = []
    for target in targets:
        registrations = list(target.detect(repo_path))
        entry: dict = {
            "id": target.id,
            "display_name": target.display_name,
            "method": None,
            "skipped": None,
            "writes": {},
        }

        method = None
        if not remove:
            method = select_install_method(target, registrations)
            if method is None:
                entry["skipped"] = "host-managed install already present"
                agents.append(entry)
                continue
            entry["method"] = method.id

        for target_scope in _scopes_for(target, scope):
            if target_scope is Scope.USER and user_scope_disabled:
                # REPOWISE_SKIP_EDITOR_SETUP means "do not touch my global
                # config", and it means it here as much as in init.
                entry["skipped"] = "user scope skipped (REPOWISE_SKIP_EDITOR_SETUP)"
                continue
            if remove:
                result = target.uninstall(target_scope, repo_path=repo_path)
            else:
                result = target.install(target_scope, repo_path=repo_path)
            entry["writes"][target_scope.value] = result.as_dict()
        agents.append(entry)

    changed = any(
        f["action"] in ("created", "updated", "removed")
        for agent in agents
        for write in agent["writes"].values()
        for f in write["files"]
    )
    return {
        "action": action,
        "repo": str(repo_path),
        "scope": scope,
        "changed": changed,
        "agents": agents,
    }


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _render_list(payload: dict) -> None:
    from rich.table import Table

    table = Table(title="Agent integrations")
    table.add_column("Agent", style="cyan")
    table.add_column("Tier")
    table.add_column("Status")
    table.add_column("Wired")

    for row in payload["agents"]:
        registrations = row["registrations"]
        if registrations:
            status = "[green]configured[/green]"
            if len(registrations) > 1:
                status = f"[yellow]configured x{len(registrations)}[/yellow]"
        elif row["present"]:
            status = "[dim]installed, not configured[/dim]"
        else:
            status = "[dim]not detected[/dim]"
        wired = ", ".join(f"{r['method']}/{r['scope']}" for r in registrations) or "-"
        table.add_row(row["id"], row["tier"], status, wired)

    console.print(table)
    if any(len(row["registrations"]) > 1 for row in payload["agents"]):
        console.print(
            "[dim]An agent wired more than once loads repowise more than once. "
            "See [bold]repowise doctor[/bold].[/dim]"
        )
    console.print("[dim]Add or remove with [bold]repowise agents add --target=<id>[/bold].[/dim]")


def _render_writes(payload: dict) -> None:
    from rich.table import Table

    verb = "Removed" if payload["action"] == "remove" else "Wired"
    table = Table(title=f"{verb} agent integrations")
    table.add_column("Agent", style="cyan")
    table.add_column("Scope")
    table.add_column("Action")
    table.add_column("File")

    for agent in payload["agents"]:
        if agent["skipped"] and not agent["writes"]:
            table.add_row(agent["id"], "-", "[dim]skipped[/dim]", agent["skipped"])
            continue
        for scope, write in agent["writes"].items():
            for entry in write["files"]:
                table.add_row(agent["id"], scope, entry["action"], entry["path"])

    console.print(table)
    for agent in payload["agents"]:
        for write in agent["writes"].values():
            for note in write["notes"]:
                console.print(f"  [yellow]{note}[/yellow]")
    if not payload["changed"]:
        console.print("[dim]Everything was already up to date.[/dim]")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group("agents", invoke_without_command=True)
@format_option(help="Output format. json reports what changed, including for writes.")
@click.pass_context
def agents_group(ctx: click.Context, fmt: str) -> None:
    """List and manage the agent integrations repowise can wire up.

    With no subcommand, lists every known agent for the current repo: its
    support tier, whether it looks installed, and every place it is currently
    wired to repowise.

    The group takes no path argument, unlike its subcommands. A group with an
    optional positional cannot tell a repo path from a subcommand name — Click
    binds ``repowise agents add`` with ``add`` as the path and then reports no
    such command. The listing runs against the current directory; every
    subcommand takes an explicit path.
    """
    if ctx.invoked_subcommand is not None:
        return

    repo_path = _repo_path(None, fmt)
    payload = {"repo": str(repo_path), "agents": describe_agents(repo_path)}
    if fmt == "json":
        emit_json(payload)
        return
    _render_list(payload)


@agents_group.command("add")
@click.argument("path", required=False, default=None)
@click.option(
    "--target",
    "target_flag",
    default=None,
    help=f"Comma-separated agent ids, or one of: {_TARGET_KEYWORDS}. Prompts when omitted.",
)
@click.option(
    "--scope",
    type=click.Choice(["project", "user", "both"]),
    default="both",
    help="Where to write: repo-local config, per-machine config, or both.",
)
@click.option("--yes", "-y", is_flag=True, default=False, help="Never prompt.")
@format_option(help="Output format. json reports every file and what happened to it.")
def agents_add(
    path: str | None,
    target_flag: str | None,
    scope: str,
    yes: bool,
    fmt: str,
) -> None:
    """Wire one or more agents up to this repo.

    ``--target=auto`` picks everything already detected. With no ``--target``
    on a terminal you get a checklist with the installed agents pre-ticked.
    """
    repo_path = _repo_path(path, fmt)
    targets = _select_targets_for_add(repo_path, target_flag, yes, fmt)
    payload = _write_payload("add", repo_path, targets, scope, remove=False)
    if fmt == "json":
        emit_json(payload)
        return
    _render_writes(payload)


def _select_targets_for_add(
    repo_path: Path,
    target_flag: str | None,
    yes: bool,
    fmt: str,
) -> list[Any]:
    """Resolve ``--target``, or ask, or fall back to what is detected."""
    from repowise.cli.agent_targets.registry import get_target

    if target_flag is not None:
        return _resolve_targets(target_flag, repo_path)

    rows = describe_agents(repo_path)
    chosen = default_selection(rows, repo_path)

    if _can_prompt(target_flag, yes) and fmt != "json":
        answered = _prompt_for_agents(rows, chosen)
        if answered is not None:
            chosen = answered

    return [t for t in (get_target(agent_id) for agent_id in chosen) if t is not None]


def _prompt_for_agents(rows: list[dict], chosen: set[str]) -> set[str] | None:
    from repowise.cli.ui.agent_selection import AgentChoice, interactive_agent_select

    return interactive_agent_select(
        console,
        [
            AgentChoice(
                id=row["id"],
                display_name=row["display_name"],
                detail=(
                    "already wired"
                    if row["registrations"]
                    else ("installed" if row["present"] else "not detected")
                ),
                enabled=row["id"] in chosen,
            )
            for row in rows
        ],
    )


@agents_group.command("remove")
@click.argument("path", required=False, default=None)
@click.option(
    "--target",
    "target_flag",
    required=True,
    help=f"Comma-separated agent ids, or one of: {_TARGET_KEYWORDS}.",
)
@click.option(
    "--scope",
    type=click.Choice(["project", "user", "both"]),
    default="both",
    help="Where to remove from.",
)
@format_option(help="Output format. json reports every file and what happened to it.")
def agents_remove(path: str | None, target_flag: str, scope: str, fmt: str) -> None:
    """Remove repowise from one or more agents.

    ``--target`` is required rather than defaulted: "remove what you detect" is
    a plausible reading of ``auto`` and a bad default for a destructive verb.
    """
    repo_path = _repo_path(path, fmt)
    targets = _resolve_targets(target_flag, repo_path)
    payload = _write_payload("remove", repo_path, targets, scope, remove=True)
    if fmt == "json":
        emit_json(payload)
        return
    _render_writes(payload)


@agents_group.command("refresh")
@click.argument("path", required=False, default=None)
@click.option(
    "--scope",
    type=click.Choice(["project", "user", "both"]),
    default="both",
    help="Which scope to refresh.",
)
@format_option(help="Output format. json reports every file and what happened to it.")
def agents_refresh(path: str | None, scope: str, fmt: str) -> None:
    """Rewrite the configs of agents that are already wired up.

    Never adds an agent. This is what to run after upgrading repowise, or after
    moving the repo: it repoints what exists and leaves everything else alone,
    so it is safe to wire into ``doctor --repair``.
    """
    from repowise.cli.agent_targets.registry import get_target

    repo_path = _repo_path(path, fmt)
    rows = describe_agents(repo_path)
    wired = [
        target
        for row in rows
        if row["registrations"] and (target := get_target(row["id"])) is not None
    ]
    payload = _write_payload("refresh", repo_path, wired, scope, remove=False)
    if fmt == "json":
        emit_json(payload)
        return
    if not wired:
        console.print("[dim]No agent is wired up yet. Run [bold]repowise agents add[/bold].[/dim]")
        return
    _render_writes(payload)


@agents_group.command("print-config")
@click.argument("target_id")
@click.argument("path", required=False, default=None)
@click.option(
    "--scope",
    type=click.Choice(["project", "user"]),
    default="project",
    help="Which scope's snippet to print.",
)
@format_option(help="Output format. table prints the bare snippet, ready to paste.")
def agents_print_config(target_id: str, path: str | None, scope: str, fmt: str) -> None:
    """Print the config snippet for an agent, writing nothing.

    This is the whole of the paste-config tier: an agent nobody has asked us to
    support properly is still served by a snippet and a docs line, at no
    maintenance cost.
    """
    from repowise.cli.agent_targets.registry import get_target, list_target_ids
    from repowise.cli.agent_targets.types import Scope

    target = get_target(target_id)
    if target is None:
        raise click.UsageError(
            f"Unknown agent id: {target_id}. Known: {', '.join(list_target_ids())}."
        )

    repo_path = _repo_path(path, fmt)
    snippet = target.print_config(Scope(scope), repo_path=repo_path)
    payload = {
        "target": target.id,
        "scope": scope,
        "repo": str(repo_path),
        "config": snippet,
        "docs_url": target.docs_url,
    }
    if fmt == "json":
        emit_json(payload)
        return
    # Bare, so it pipes into a file or a clipboard without a table around it.
    click.echo(payload["config"])
