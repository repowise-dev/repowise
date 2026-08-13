"""``repowise uninstall`` — remove repowise from this repo and this machine.

Two motives brought this command about and they look opposed: convenience for a
reinstall wants the index kept, and a trust signal wants everything gone. They
are not opposed, because **the trust signal is the report, not the deletion.**
A command that enumerates every path repowise has ever written, removes what was
chosen, and states exactly what it left and why is completely honest even when it
leaves the index. Completeness of the inventory is the claim; which boxes start
ticked is a separate knob.

**There is no ``--yes``.** Everywhere else in this CLI that flag means "run the
default without asking", and there is no safe meaning for it here. If it ran the
default set, someone who typed it to uninstall would get a partial uninstall. If
it removed everything, it would mean something different from ``agents add -y``
and ``init --yes``, and that difference is discoverable only by losing an index.
So the non-interactive path names its own scope, and naming the scope is the
consent: ``--all`` or ``--keep-index``. The flag is still accepted, purely to
fail with a sentence saying which one to use, because a helpful error beats a
surprising deletion.

**Consent is never inferred from a tty probe.** ``isatty`` lies on Windows, and
the two ways to be wrong are not symmetric: guessing "interactive" hangs, and
guessing "not interactive" deletes without asking. With no terminal and no scope
flag this prints the inventory, removes nothing, and exits non-zero.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from repowise.cli.agent_targets.types import FileAction
from repowise.cli.helpers import console, resolve_command_target
from repowise.cli.output import emit_json, format_option
from repowise.cli.uninstall import build_plan, execute
from repowise.cli.uninstall.inventory import ALL_GROUPS, DEFAULT_GROUPS, Group, Plan
from repowise.cli.uninstall.runner import EXIT_NEEDS_SCOPE, Outcome

_ACTION_STYLE = {
    FileAction.REMOVED.value: "green",
    FileAction.NOT_FOUND.value: "dim",
    FileAction.KEPT.value: "yellow",
    FileAction.FAILED.value: "red",
}


def _resolve_repo(path: str | None) -> Path:
    """Resolve the repo, and deliberately skip the update notice.

    Every other command calls ``target.notice`` here. This one must not, for two
    reasons. Advertising an upgrade to someone uninstalling is the wrong thing
    to say. And the notice caches its result in
    ``~/.repowise/update-check.json``, which recreates the very directory a
    previous ``--all`` run deleted, so the *second* run would find machine-wide
    state, report it removed again, and never converge.
    """
    target = resolve_command_target(path=path, workspace_flag=False, no_workspace_flag=True)
    assert target.repo_path is not None
    return target.repo_path


def _group_choices(plan: Plan, preticked: frozenset[Group]) -> list:
    """One checklist row per group that has something on disk.

    Groups with nothing present are left out of the prompt rather than shown
    ticked and inert: a checklist offering to delete four things when three of
    them do not exist teaches the user not to read it. They still appear in the
    printed inventory as not-found, which is where "we looked here" belongs.
    """
    from repowise.cli.ui.agent_selection import AgentChoice

    labels = {
        Group.AGENTS: ("Agent configuration", "every wired agent, both scopes"),
        Group.REPO_FILES: ("Generated blocks", "the managed sections in CLAUDE.md and AGENTS.md"),
        Group.INDEX: ("Repo index", "rebuilding it costs a full re-index"),
        Group.GLOBAL: ("Machine-wide state", "login, caches, telemetry preference"),
    }
    choices = []
    for group in plan.groups_present():
        name, detail = labels[group]
        size = sum(item.size or 0 for item in plan.for_groups(frozenset({group})))
        if size:
            detail = f"{detail}, {_human_size(size)}"
        choices.append(
            AgentChoice(id=group.value, display_name=name, detail=detail, enabled=group in preticked)
        )
    return choices


def _human_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    raise AssertionError("unreachable: the GB arm always returns")


def _render_plan(plan: Plan, groups: frozenset[Group], *, heading: str) -> None:
    from rich.table import Table

    table = Table(title=heading)
    table.add_column("Group", style="cyan")
    table.add_column("Path")
    table.add_column("Note")

    for group in Group:
        for item in plan.for_groups(frozenset({group})):
            if group not in groups:
                note = "[dim]not selected[/dim]"
            elif item.blocked:
                note = f"[yellow]{item.blocked}[/yellow]"
            elif not item.exists:
                note = "[dim]not found[/dim]"
            else:
                note = _human_size(item.size) if item.size else ""
            table.add_row(group.value, str(item.path), note)
    console.print(table)


def _render_outcome(outcome: Outcome) -> None:
    from rich.table import Table

    table = Table(title="Uninstall")
    table.add_column("Group", style="cyan")
    table.add_column("Action")
    table.add_column("Path")

    for result in outcome.results:
        style = _ACTION_STYLE.get(result.action.value, "")
        action = f"[{style}]{result.action.value}[/{style}]" if style else result.action.value
        cell = str(result.path)
        if result.reason:
            cell = f"{cell}\n[dim]{result.reason}[/dim]"
        elif result.action is FileAction.KEPT:
            cell = f"{cell}\n[dim]no reason recorded[/dim]"
        table.add_row(result.group.value, action, cell)

    # Printed even when every row is not-found. A trust command that prints
    # nothing on a clean machine has proven nothing about where it looked.
    console.print(table)


def _print_advisories(plan: Plan, outcome: Outcome | None) -> None:
    for note in outcome.notes if outcome else []:
        console.print(f"  [yellow]{note}[/yellow]")
    for line in plan.advisories:
        console.print(f"  [dim]{line}[/dim]")


@click.command("uninstall")
@click.argument("path", required=False, default=None)
@click.option(
    "--all",
    "remove_all",
    is_flag=True,
    help="Remove everything, including the repo index and machine-wide state.",
)
@click.option(
    "--keep-index",
    is_flag=True,
    help=(
        "The reinstall case: remove agent wiring and the generated blocks, "
        "keeping the repo index and machine-wide state (login, caches)."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the plan and change nothing.",
)
@click.option(
    "--yes",
    "-y",
    "yes",
    is_flag=True,
    hidden=True,
    help="Not a flag on this command. Say --all or --keep-index instead.",
)
@format_option(help="Output format. json reports the plan, every path and what happened to it.")
@click.pass_context
def uninstall_command(
    ctx: click.Context,
    path: str | None,
    remove_all: bool,
    keep_index: bool,
    dry_run: bool,
    yes: bool,
    fmt: str,
) -> None:
    """Remove repowise from this repo, and optionally from this machine.

    With a terminal and no flags this shows what repowise has written and asks
    what to remove. Non-interactively, say what you want removed:

    \b
      repowise uninstall --all           everything
      repowise uninstall --keep-index    everything except the index

    The package itself and the Claude Code plugin are owned by their installers,
    not by us. The report names the command for each.
    """
    # Before anything else, including the argument checks, because the cost of
    # being late is silent and permanent. Telemetry spools to
    # ``~/.repowise/telemetry-spool.jsonl`` and consent state lives in
    # ``~/.repowise/platform.json``, and both are written on the way out of a
    # command. Without this, ``uninstall --all`` deleted the directory, reported
    # it removed truthfully, and then our own process recreated it holding a
    # fresh anonymous id and a spool file. The report was accurate at the moment
    # it was printed and wrong by the time the shell prompt came back.
    #
    # An env var rather than a config write, because the config lives in the
    # directory we are about to delete.
    import os

    os.environ["REPOWISE_TELEMETRY_DISABLED"] = "1"

    if remove_all and keep_index:
        raise click.UsageError("--all and --keep-index say opposite things. Pick one.")
    if yes:
        raise click.UsageError(
            "uninstall has no --yes: on every other command it means 'run the default', "
            "and a partial uninstall is not a safe default. Say what to remove: "
            "--all, or --keep-index to keep the repo index."
        )

    repo_path = _resolve_repo(path)
    plan = build_plan(repo_path)

    if remove_all:
        groups = ALL_GROUPS
    elif keep_index:
        # The reinstall case, so it keeps machine-wide state too. Reading
        # ``--keep-index`` as "everything except the index" would take the
        # user's login and the shared embedder key on the way past, which is
        # precisely what someone about to reinstall does not want. It also
        # gives the command a property worth having on its own: machine-wide
        # state goes only when the user names it, by ``--all`` or by ticking it.
        groups = frozenset(DEFAULT_GROUPS)
    elif dry_run:
        # Only when no scope flag was given: there is nothing to honour and
        # nobody to ask, so show everything. This sits *after* `--keep-index`
        # on purpose. Ahead of it, `--keep-index --dry-run` reported that the
        # index and the login would go, which is the opposite of what the real
        # run does and exactly the pre-flight a cautious user leans on.
        groups = ALL_GROUPS
    else:
        groups = _ask(plan, ctx, fmt)

    if dry_run:
        _emit_dry_run(plan, groups, fmt)
        return

    outcome = execute(plan, groups)
    if fmt == "json":
        emit_json(
            {
                "action": "uninstall",
                "dry_run": False,
                "repo": str(repo_path),
                "groups": sorted(group.value for group in groups),
                "plan": plan.as_dict(),
                **outcome.as_dict(),
            }
        )
    else:
        _render_outcome(outcome)
        _print_advisories(plan, outcome)
        if outcome.complete:
            console.print("[green]Everything selected is gone.[/green]")
        else:
            console.print(
                f"[yellow]{len(outcome.leftovers)} path(s) still here. "
                "See the reason on each row.[/yellow]"
            )
    ctx.exit(outcome.exit_code)


def _emit_dry_run(plan: Plan, groups: frozenset[Group], fmt: str) -> None:
    """The plan, and nothing else.

    Deliberately the same plan object a real run executes, projected the same
    way, so the two cannot describe different work. ``--dry-run`` is not a
    separate code path that has to be kept in step with the real one.
    """
    if fmt == "json":
        emit_json(
            {
                "action": "uninstall",
                "dry_run": True,
                "repo": str(plan.repo_path),
                "groups": sorted(group.value for group in groups),
                "plan": plan.as_dict(),
            }
        )
        return
    _render_plan(plan, groups, heading="Uninstall plan (dry run, nothing was changed)")
    _print_advisories(plan, None)


def _ask(plan: Plan, ctx: click.Context, fmt: str) -> frozenset[Group]:
    """The interactive path, and its two refusals.

    Refuses in both directions rather than guessing. With no terminal, or with
    ``--format json``, there is nobody to answer and the command will not pick a
    destructive default on their behalf. And when the prompt itself comes back
    unanswerable, this aborts where ``agents add`` carries on with the ticked
    set: that fallback is right for adding and wrong for anything that deletes.
    """
    from repowise.cli.ui.agent_selection import interactive_agent_select

    interactive = fmt != "json"
    try:
        interactive = interactive and sys.stdin.isatty()
    except Exception:
        interactive = False

    if not interactive:
        if fmt == "json":
            # A rich table here printed box-drawing characters onto the same
            # stream as the payload, so `--format json` with no scope flag
            # produced output no parser could read. The refusal is data too.
            emit_json(
                {
                    "action": "uninstall",
                    "dry_run": True,
                    "repo": str(plan.repo_path),
                    "groups": [],
                    "plan": plan.as_dict(),
                    "error": "uninstall needs a scope: pass --all or --keep-index",
                }
            )
            ctx.exit(EXIT_NEEDS_SCOPE)
        _render_plan(
            plan,
            ALL_GROUPS,
            heading="Nothing was removed. This is what repowise has written.",
        )
        _print_advisories(plan, None)
        console.print(
            "[yellow]Say what to remove: --all, or --keep-index to keep the repo index. "
            "Add --dry-run to see this again without being asked.[/yellow]"
        )
        ctx.exit(EXIT_NEEDS_SCOPE)

    _render_plan(plan, ALL_GROUPS, heading="What repowise has written")
    _print_advisories(plan, None)

    choices = _group_choices(plan, DEFAULT_GROUPS)
    if not choices:
        console.print("[dim]Nothing of ours is here.[/dim]")
        ctx.exit(0)

    chosen = interactive_agent_select(
        console,
        choices,
        title="[bold]Uninstall:[/bold] remove which of these?",
        hint="Enter to accept the ticked set, or numbers to toggle (1,3), 'all', 'none'.",
        prompt="  Remove",
    )
    if chosen is None:
        console.print("[yellow]Could not read an answer, so nothing was removed.[/yellow]")
        ctx.exit(EXIT_NEEDS_SCOPE)
    if not chosen:
        console.print("[dim]Nothing selected, so nothing was removed.[/dim]")
        ctx.exit(0)

    groups = frozenset(Group(value) for value in chosen)

    # A second, explicit yes. The checklist has a fallback this command must not
    # inherit: unparseable input keeps the ticked set and carries on, which is
    # right for `agents add` and means `asdf` deletes things here. A typed
    # confirmation turns every ambiguous answer into a definite one, and
    # defaults to no.
    console.print()
    for group in sorted(groups, key=list(Group).index):
        console.print(f"  [red]remove[/red] {group.value}")
    if not click.confirm("Remove these?", default=False):
        console.print("[dim]Nothing was removed.[/dim]")
        ctx.exit(0)
    return groups
