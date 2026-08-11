"""``repowise hook`` — manage git post-commit hooks and agent hooks."""

from __future__ import annotations

import click

from repowise.cli.helpers import (
    console,
    resolve_command_target,
)
from repowise.cli.output import (
    emit_json,
    format_option,
    json_option,
    notice_console,
    resolve_format,
)

#: Every ``hook stats --format json`` payload carries these five keys, whether
#: or not there is a ledger to fill them from. A consumer that has to check
#: which keys exist before reading them is not much better off than one parsing
#: a table, so the empty case is the full shape rather than a shorter one.
_EMPTY_STATS: dict = {
    "surfaces": [],
    "runs": [],  # hook_run_by_tool
    "decision_feedback": {},  # decision_feedback_totals
    "builds": [],  # injection_builds
    "rewrite": [],  # rewrite_run_totals
}


@click.group("hook")
def hook_group() -> None:
    """Manage git hooks for auto-sync and agent hooks for distill."""


def _hook_target(
    path: str | None,
    workspace: bool,
    no_workspace: bool,
):
    """Resolve the target for a hook subcommand."""
    target = resolve_command_target(
        path=path,
        workspace_flag=workspace,
        no_workspace_flag=no_workspace,
    )
    target.notice(console, command="hook")
    return target


@hook_group.command("install")
@click.argument("path", required=False, default=None)
@click.option(
    "--workspace",
    "-w",
    is_flag=True,
    default=False,
    help="Force workspace mode (install hooks for every repo in the workspace).",
)
@click.option(
    "--no-workspace",
    is_flag=True,
    default=False,
    help="Force single-repo mode even when invoked from a workspace.",
)
def hook_install(path: str | None, workspace: bool, no_workspace: bool) -> None:
    """Install a post-commit hook that auto-syncs after every commit."""
    from repowise.cli.hooks import install

    target = _hook_target(path, workspace, no_workspace)

    if target.is_workspace:
        assert target.ws_root is not None and target.ws_config is not None
        for entry in target.ws_config.repos:
            abs_path = (target.ws_root / entry.path).resolve()
            result = install(abs_path)
            console.print(f"  {entry.alias}: [green]{result}[/green]")
    else:
        assert target.repo_path is not None
        result = install(target.repo_path)
        console.print(f"Post-commit hook: [green]{result}[/green]")


@hook_group.command("uninstall")
@click.argument("path", required=False, default=None)
@click.option(
    "--workspace",
    "-w",
    is_flag=True,
    default=False,
    help="Force workspace mode (uninstall hooks from every repo in the workspace).",
)
@click.option(
    "--no-workspace",
    is_flag=True,
    default=False,
    help="Force single-repo mode even when invoked from a workspace.",
)
def hook_uninstall(path: str | None, workspace: bool, no_workspace: bool) -> None:
    """Remove the repowise post-commit hook."""
    from repowise.cli.hooks import uninstall

    target = _hook_target(path, workspace, no_workspace)

    if target.is_workspace:
        assert target.ws_root is not None and target.ws_config is not None
        for entry in target.ws_config.repos:
            abs_path = (target.ws_root / entry.path).resolve()
            result = uninstall(abs_path)
            console.print(f"  {entry.alias}: {result}")
    else:
        assert target.repo_path is not None
        result = uninstall(target.repo_path)
        console.print(f"Post-commit hook: {result}")


@hook_group.group("rewrite")
def rewrite_group() -> None:
    """Manage the distill command-rewrite hook (Claude Code + Codex).

    When installed, noisy commands an agent runs (tests, builds, git
    status/log/diff, searches, listings) are rewritten to
    ``repowise distill <command>`` so the agent sees a compact, errors-first
    rendering. Raw output stays recoverable via ``repowise expand <ref>``.

    The default posture is ``allow``: rewrites run without a prompt, and only
    ever wrap a command already recognized as one of the distill families.
    Set ``permission: ask`` under ``distill.commands`` in
    ``.repowise/config.yaml`` to approve each one instead. Codex hooks cannot
    show a rewritten command for approval at all, so a family set to ``ask``
    simply passes through there; every Codex install also maintains an
    AGENTS.md awareness section that works without any hook.
    """


def _target_repo_paths(target) -> list:
    """The repo paths a hook subcommand should act on (repowise repos only)."""
    if target.is_workspace:
        assert target.ws_root is not None and target.ws_config is not None
        return [
            (target.ws_root / entry.path).resolve()
            for entry in target.ws_config.repos
            if ((target.ws_root / entry.path).resolve() / ".repowise").is_dir()
        ]
    assert target.repo_path is not None
    return [target.repo_path] if (target.repo_path / ".repowise").is_dir() else []


def _print_rewrite_hook_status(label: str, status) -> None:
    """Report one agent's rewrite hook, separating registered from live.

    Presence keys on the hook command, so an entry whose matcher names a tool
    the agent has since renamed reads as "installed" while firing on nothing.
    A hook that matches nothing is silent in exactly the way a working one is,
    which is why this has to be said rather than inferred.
    """
    if not status.installed:
        console.print(f"  [dim]✗[/dim] {label} rewrite hook: not installed")
        return
    if not status.unmatched:
        console.print(f"  [green]✓[/green] {label} rewrite hook: installed")
        return
    missed = ", ".join(status.unmatched)
    state = "registered but inert" if not status.fires else "installed, matcher too narrow"
    console.print(f"  [yellow]![/yellow] {label} rewrite hook: {state}")
    console.print(
        f"      [dim]its matcher ({status.matcher!r}) does not select {missed}, so "
        f"commands {label} runs under {'that name' if len(status.unmatched) == 1 else 'those names'} "
        "pass through unrewritten. `repowise hook rewrite install` repoints a "
        "matcher it recognises as its own; a hand-narrowed one has to be "
        "widened by hand.[/dim]"
    )


def _codex_capability_note(version, supports) -> str:
    """One honest line about what the local Codex build can actually do."""
    from repowise.cli.editor_integrations.codex_config import CODEX_REWRITE_MIN_VERSION

    min_str = ".".join(str(v) for v in CODEX_REWRITE_MIN_VERSION)
    if supports is None:
        return "Codex CLI not found on PATH — rewrite support unknown"
    ver_str = ".".join(str(v) for v in version)
    if not supports:
        return (
            f"Codex {ver_str} cannot rewrite commands (needs >= {min_str}); "
            "AGENTS.md awareness section only"
        )
    return (
        f"Codex {ver_str}: rewrites apply only to families set to "
        "`permission: allow` — Codex cannot ask-with-rewrite, `ask` families "
        "pass through"
    )


@rewrite_group.command("install")
@click.argument("path", required=False, default=None)
@click.option(
    "--workspace",
    "-w",
    is_flag=True,
    default=False,
    help="Force workspace mode (re-enable distill rewrites for every repo in the workspace).",
)
@click.option(
    "--no-workspace",
    is_flag=True,
    default=False,
    help="Force single-repo mode even when invoked from a workspace.",
)
@click.option(
    "--allow-rule/--no-allow-rule",
    "allow_rule",
    default=None,
    help=(
        "Seed a Claude Code permission allow rule for `repowise distill` "
        "commands. Only needed if you set `permission: ask` in "
        ".repowise/config.yaml; the default `allow` posture rewrites without "
        "a prompt and needs no allow rule."
    ),
)
def rewrite_install(
    path: str | None, workspace: bool, no_workspace: bool, allow_rule: bool | None
) -> None:
    """Install the rewrite hook into ~/.claude/settings.json.

    The hook itself is user-level (one install covers every repo); this
    command additionally re-enables ``distill.commands.enabled`` for the
    target — every workspace repo in workspace mode, the target repo
    otherwise — since a prior ``repowise init`` opt-out may have gated
    repos off.
    """
    from repowise.cli.agent_adapters.claude_code import ClaudeCodeAdapter
    from repowise.cli.helpers import save_distill_commands_enabled

    target = _hook_target(path, workspace, no_workspace)

    hook_path = ClaudeCodeAdapter().install_rewrite_hook()
    if not hook_path:
        console.print("Rewrite hook: [red]install failed[/red]")
        return
    console.print(f"Rewrite hook: [green]installed[/green] ({hook_path})")
    console.print(
        "  [dim]Per-repo behavior is configured under `distill.commands` "
        "in .repowise/config.yaml (permission: allow | ask).[/dim]"
    )
    console.print(
        "  [dim]Rewrites run without a prompt by default (for the main agent "
        "and subagents alike); set `permission: ask` to review each one.[/dim]"
    )

    # The default `allow` posture rewrites without a prompt, so no allowlist
    # entry is needed. Seeding `Bash(repowise distill:*)` only helps users who
    # set `permission: ask` and want their existing allowlist (e.g.
    # `Bash(git diff:*)`) to keep matching the rewritten string — honor it
    # only when explicitly requested via --allow-rule.
    if allow_rule:
        from repowise.cli.editor_integrations.claude_config import (
            add_claude_code_distill_allow_rules,
        )

        settings = add_claude_code_distill_allow_rules()
        if settings:
            console.print(f"  [green]✓[/green] Allow rule added ({settings})")
        else:
            console.print("  [yellow]Could not update permission rules.[/yellow]")

    if target.is_workspace:
        assert target.ws_root is not None and target.ws_config is not None
        for entry in target.ws_config.repos:
            abs_path = (target.ws_root / entry.path).resolve()
            if (abs_path / ".repowise").is_dir():
                save_distill_commands_enabled(abs_path, enabled=True)
                console.print(f"  {entry.alias}: [green]enabled[/green]")
    else:
        assert target.repo_path is not None
        if (target.repo_path / ".repowise").is_dir():
            save_distill_commands_enabled(target.repo_path, enabled=True)

    _install_codex_surfaces(target)


def _install_codex_surfaces(target) -> None:
    """Codex side of ``rewrite install``: version-gated hook + awareness section.

    Skipped silently when the user doesn't use Codex (no ``~/.codex``). The
    hooks.json entry installs only on a build that honors ``updatedInput``
    rewrites; the AGENTS.md awareness section installs regardless, because it
    needs no hook support at all.
    """
    from repowise.cli.agent_adapters.codex import CodexAdapter

    codex = CodexAdapter()
    if not codex.detect():
        return

    from repowise.cli.editor_integrations.codex_config import (
        codex_cli_version,
        codex_supports_rewrite,
        install_agents_md_distill_section,
    )

    version = codex_cli_version()
    supports = codex_supports_rewrite(version)
    if supports:
        codex_path = codex.install_rewrite_hook()
        if codex_path:
            console.print(f"Codex rewrite hook: [green]installed[/green] ({codex_path})")
            console.print(f"  [dim]{_codex_capability_note(version, supports)}.[/dim]")
            console.print(
                "  [dim]Codex requires new hooks to be reviewed — run /hooks "
                "inside Codex to trust it.[/dim]"
            )
        else:
            console.print("Codex rewrite hook: [red]install failed[/red]")
    else:
        console.print(
            f"Codex rewrite hook: [yellow]skipped[/yellow] — "
            f"{_codex_capability_note(version, supports)}."
        )

    for repo_path in _target_repo_paths(target):
        agents_path = install_agents_md_distill_section(repo_path)
        if agents_path:
            console.print(f"  [green]✓[/green] AGENTS.md distill section ({agents_path})")
        else:
            console.print(f"  [yellow]AGENTS.md distill section failed ({repo_path})[/yellow]")


@rewrite_group.command("uninstall")
@click.argument("path", required=False, default=None)
@click.option(
    "--workspace",
    "-w",
    is_flag=True,
    default=False,
    help="Force workspace mode (remove the AGENTS.md section from every repo).",
)
@click.option(
    "--no-workspace",
    is_flag=True,
    default=False,
    help="Force single-repo mode even when invoked from a workspace.",
)
def rewrite_uninstall(path: str | None, workspace: bool, no_workspace: bool) -> None:
    """Remove the rewrite hooks and the AGENTS.md awareness section."""
    from repowise.cli.agent_adapters.claude_code import ClaudeCodeAdapter
    from repowise.cli.agent_adapters.codex import CodexAdapter

    removed = ClaudeCodeAdapter().uninstall_rewrite_hook()
    console.print(f"Rewrite hook: {'[green]removed[/green]' if removed else 'not installed'}")

    codex = CodexAdapter()
    if codex.detect():
        codex_removed = codex.uninstall_rewrite_hook()
        console.print(
            f"Codex rewrite hook: {'[green]removed[/green]' if codex_removed else 'not installed'}"
        )
        from repowise.cli.editor_integrations.codex_config import (
            remove_agents_md_distill_section,
        )

        target = _hook_target(path, workspace, no_workspace)
        for repo_path in _target_repo_paths(target):
            if remove_agents_md_distill_section(repo_path):
                console.print(f"  [green]✓[/green] AGENTS.md distill section removed ({repo_path})")


@rewrite_group.command("status")
@click.argument("path", required=False, default=None)
@click.option(
    "--workspace",
    "-w",
    is_flag=True,
    default=False,
    help="Force workspace mode (report the AGENTS.md section for every repo).",
)
@click.option(
    "--no-workspace",
    is_flag=True,
    default=False,
    help="Force single-repo mode even when invoked from a workspace.",
)
def rewrite_status(path: str | None, workspace: bool, no_workspace: bool) -> None:
    """Check the rewrite hooks and what each agent can actually do."""
    from repowise.cli.agent_adapters.claude_code import ClaudeCodeAdapter
    from repowise.cli.agent_adapters.codex import CodexAdapter

    _print_rewrite_hook_status("claude-code", ClaudeCodeAdapter().rewrite_hook_status())

    codex = CodexAdapter()
    if not codex.detect():
        console.print("  [dim]✗[/dim] codex: not detected (no ~/.codex)")
        return

    from repowise.cli.editor_integrations.codex_config import (
        agents_md_distill_section_installed,
        codex_cli_version,
        codex_supports_rewrite,
    )

    version = codex_cli_version()
    supports = codex_supports_rewrite(version)
    _print_rewrite_hook_status("codex", codex.rewrite_hook_status())
    console.print(f"      [dim]{_codex_capability_note(version, supports)}[/dim]")

    target = _hook_target(path, workspace, no_workspace)
    for repo_path in _target_repo_paths(target):
        section = agents_md_distill_section_installed(repo_path)
        icon = "[green]✓[/green]" if section else "[dim]✗[/dim]"
        state = "installed" if section else "not installed"
        console.print(f"  {icon} AGENTS.md distill section: {state} ({repo_path})")


@hook_group.group("read-skeleton")
def read_skeleton_group() -> None:
    """Manage skeleton-served Reads (Claude Code).

    An unbounded Read of a large indexed file comes back as its skeleton —
    signatures kept, bodies elided, every elided span carrying the line range
    that reads it back. Reading the same file a second time returns it whole.

    There is no hook to install: the PostToolUse hook `repowise init` already
    set up carries this. What these commands move is the per-repo verdict
    `hooks.read_skeleton` in .repowise/config.yaml, which `repowise init`
    writes from the same answer as the rewrite hook. Use these to change your
    mind about one repo without re-running init.
    """


@read_skeleton_group.command("install")
@click.argument("path", required=False, default=None)
@click.option("--workspace", "-w", is_flag=True, default=False, help="Force workspace mode.")
@click.option("--no-workspace", is_flag=True, default=False, help="Force single-repo mode.")
def read_skeleton_install(path: str | None, workspace: bool, no_workspace: bool) -> None:
    """Serve large indexed Reads as skeletons in this repo."""
    _set_read_skeleton(path, workspace, no_workspace, enabled=True)


@read_skeleton_group.command("uninstall")
@click.argument("path", required=False, default=None)
@click.option("--workspace", "-w", is_flag=True, default=False, help="Force workspace mode.")
@click.option("--no-workspace", is_flag=True, default=False, help="Force single-repo mode.")
def read_skeleton_uninstall(path: str | None, workspace: bool, no_workspace: bool) -> None:
    """Stop replacing Reads in this repo; they come back whole."""
    _set_read_skeleton(path, workspace, no_workspace, enabled=False)


def _set_read_skeleton(
    path: str | None, workspace: bool, no_workspace: bool, *, enabled: bool
) -> None:
    """Write ``hooks.read_skeleton`` for the target repo or workspace."""
    _set_hook_surface(
        path, workspace, no_workspace, surface="read_skeleton", label="Skeleton-served Reads", enabled=enabled
    )


def _set_hook_surface(
    path: str | None,
    workspace: bool,
    no_workspace: bool,
    *,
    surface: str,
    label: str,
    enabled: bool,
) -> None:
    """Write ``hooks.<surface>`` for the target repo or workspace."""
    from repowise.cli.helpers import save_hook_surface_enabled

    target = _hook_target(path, workspace, no_workspace)
    word = "[green]on[/green]" if enabled else "[yellow]off[/yellow]"
    touched = 0
    for repo_path in _target_repo_paths(target):
        if not (repo_path / ".repowise").is_dir():
            continue
        save_hook_surface_enabled(repo_path, surface, enabled=enabled)
        console.print(f"  {label}: {word} ({repo_path})")
        touched += 1
    if not touched:
        console.print("  [yellow]No indexed repo here — run `repowise init` first.[/yellow]")


@read_skeleton_group.command("status")
@click.argument("path", required=False, default=None)
@click.option("--workspace", "-w", is_flag=True, default=False, help="Force workspace mode.")
@click.option("--no-workspace", is_flag=True, default=False, help="Force single-repo mode.")
def read_skeleton_status(path: str | None, workspace: bool, no_workspace: bool) -> None:
    """Report whether Reads are being served as skeletons, and what it saved."""
    from repowise.cli.commands.augment_cmd.read_skeleton import enabled as read_skeleton_enabled

    target = _hook_target(path, workspace, no_workspace)
    for repo_path in _target_repo_paths(target):
        on = read_skeleton_enabled(repo_path)
        icon = "[green]✓[/green]" if on else "[dim]✗[/dim]"
        console.print(f"  {icon} skeleton-served Reads: {'on' if on else 'off'} ({repo_path})")
        if not on:
            # The counterfactual is the whole point of measuring while off:
            # a repo that declined can still see what declining costs it.
            console.print(
                "      [dim]`repowise saved` shows what this would have saved "
                "if it were on.[/dim]"
            )


@hook_group.group("read-reread")
def read_reread_group() -> None:
    """Manage collapsed re-reads (Claude Code).

    Reading a file the session has already read, with no edit in between and
    the bytes unchanged, returns a short notice naming the earlier read
    instead of the content — which is already a few tool calls up in context.

    Nothing is guessed: the served bytes are hashed, and a file whose content
    differs is served in full, with a line saying it changed underneath you.
    Reading again always returns the content, so a compaction that dropped the
    earlier copy costs one extra Read and nothing else.

    Like `read-skeleton`, there is no hook to install. This moves the per-repo
    verdict `hooks.read_reread`.
    """


@read_reread_group.command("install")
@click.argument("path", required=False, default=None)
@click.option("--workspace", "-w", is_flag=True, default=False, help="Force workspace mode.")
@click.option("--no-workspace", is_flag=True, default=False, help="Force single-repo mode.")
def read_reread_install(path: str | None, workspace: bool, no_workspace: bool) -> None:
    """Collapse unchanged re-reads to a notice in this repo."""
    _set_hook_surface(
        path, workspace, no_workspace,
        surface="read_reread", label="Collapsed re-reads", enabled=True,
    )


@read_reread_group.command("uninstall")
@click.argument("path", required=False, default=None)
@click.option("--workspace", "-w", is_flag=True, default=False, help="Force workspace mode.")
@click.option("--no-workspace", is_flag=True, default=False, help="Force single-repo mode.")
def read_reread_uninstall(path: str | None, workspace: bool, no_workspace: bool) -> None:
    """Stop collapsing re-reads in this repo; they come back whole."""
    _set_hook_surface(
        path, workspace, no_workspace,
        surface="read_reread", label="Collapsed re-reads", enabled=False,
    )


@read_reread_group.command("status")
@click.argument("path", required=False, default=None)
@click.option("--workspace", "-w", is_flag=True, default=False, help="Force workspace mode.")
@click.option("--no-workspace", is_flag=True, default=False, help="Force single-repo mode.")
def read_reread_status(path: str | None, workspace: bool, no_workspace: bool) -> None:
    """Report whether unchanged re-reads are being collapsed."""
    from repowise.cli.commands.augment_cmd.reread import enabled as reread_enabled

    target = _hook_target(path, workspace, no_workspace)
    for repo_path in _target_repo_paths(target):
        on = reread_enabled(repo_path)
        icon = "[green]✓[/green]" if on else "[dim]✗[/dim]"
        console.print(f"  {icon} collapsed re-reads: {'on' if on else 'off'} ({repo_path})")
        if not on:
            console.print(
                "      [dim]`repowise saved` shows what this would have saved "
                "if it were on.[/dim]"
            )


@hook_group.group("search-digest")
def search_digest_group() -> None:
    """Manage digest-served searches (Claude Code).

    A grep that floods across many files comes back as a compact per-file
    digest: every file named with its match count and anchor line numbers,
    and the dropped tail counted, instead of the raw match list. Re-run the
    search scoped to a file to see its matches in full.

    Single-file context greps (`-C`/`-A`/`-B`) are never touched: that context
    is what the agent asked for.

    Like `read-skeleton`, there is no hook to install. This moves the per-repo
    verdict `hooks.search_digest`, which `repowise init` writes from the same
    answer as the rewrite hook.
    """


@search_digest_group.command("install")
@click.argument("path", required=False, default=None)
@click.option("--workspace", "-w", is_flag=True, default=False, help="Force workspace mode.")
@click.option("--no-workspace", is_flag=True, default=False, help="Force single-repo mode.")
def search_digest_install(path: str | None, workspace: bool, no_workspace: bool) -> None:
    """Serve multi-file grep floods as digests in this repo."""
    _set_hook_surface(
        path, workspace, no_workspace,
        surface="search_digest", label="Digest-served searches", enabled=True,
    )


@search_digest_group.command("uninstall")
@click.argument("path", required=False, default=None)
@click.option("--workspace", "-w", is_flag=True, default=False, help="Force workspace mode.")
@click.option("--no-workspace", is_flag=True, default=False, help="Force single-repo mode.")
def search_digest_uninstall(path: str | None, workspace: bool, no_workspace: bool) -> None:
    """Stop replacing search floods in this repo; the digest goes back to riding alongside."""
    _set_hook_surface(
        path, workspace, no_workspace,
        surface="search_digest", label="Digest-served searches", enabled=False,
    )


@search_digest_group.command("status")
@click.argument("path", required=False, default=None)
@click.option("--workspace", "-w", is_flag=True, default=False, help="Force workspace mode.")
@click.option("--no-workspace", is_flag=True, default=False, help="Force single-repo mode.")
def search_digest_status(path: str | None, workspace: bool, no_workspace: bool) -> None:
    """Report whether search floods are being served as digests."""
    from repowise.cli.commands.augment_cmd.search_digest import enabled as search_digest_enabled

    target = _hook_target(path, workspace, no_workspace)
    for repo_path in _target_repo_paths(target):
        on = search_digest_enabled(repo_path)
        icon = "[green]✓[/green]" if on else "[dim]✗[/dim]"
        console.print(f"  {icon} digest-served searches: {'on' if on else 'off'} ({repo_path})")
        if not on:
            console.print(
                "      [dim]`repowise saved` shows what this would have saved "
                "if it were on.[/dim]"
            )


def _print_rewrite(rows: list[dict]) -> None:
    """The PreToolUse rewrite hook's own table: what it wrapped and what it let by.

    Its own block rather than a row in the efficacy table above, because the
    two measure different things and the columns do not transfer. An emission
    surface is judged on whether the agent acted; this one either rewrote a
    command or did not, and the interesting number is the reason it did not.
    """
    if not rows:
        return
    from rich.table import Table

    from repowise.cli.hook_ledger import REWRITTEN

    rewritten = sum(r["calls"] for r in rows if r["outcome"] == REWRITTEN)
    total = sum(r["calls"] for r in rows)
    table = Table(title="Command rewrite hook (PreToolUse)", header_style="bold")
    table.add_column("outcome/reason")
    table.add_column("commands", justify="right")
    table.add_column("share", justify="right")
    table.add_column("sessions", justify="right")
    table.add_column("median ms", justify="right")
    for row in rows:
        share = 100.0 * row["calls"] / total if total else 0.0
        colour = "green" if row["outcome"] == REWRITTEN else "dim"
        per_call = row["total_ms"] // row["calls"] if row["calls"] else 0
        table.add_row(
            f"[{colour}]{row['outcome']}/{row['reason']}[/{colour}]",
            f"{row['calls']:,}",
            f"{share:.1f}%",
            f"{row['sessions']:,}",
            str(per_call),
        )
    console.print(table)
    pct = 100.0 * rewritten / total if total else 0.0
    console.print(
        f"  [dim]{rewritten:,} of {total:,} shell commands rewritten ({pct:.1f}%). "
        "Commands run outside an indexed repo are not counted — there is no "
        "ledger there to count them in.[/dim]"
    )


def _print_builds(builds: list[dict]) -> None:
    """Name the builds behind these firings; loud when there is more than one.

    A ledger with two live builds in it is not a curiosity: an emitter deleted
    in one install can still be firing from the other, and from a transcript
    alone that is indistinguishable from a retirement that never happened. One
    build is a one-line footnote; two is a warning, and the rows above cannot
    be read as one population until it is resolved.
    """
    if not builds:
        return
    stamped = [b for b in builds if b["build"]]
    unstamped = next((b for b in builds if not b["build"]), None)
    if unstamped is not None:
        console.print(
            f"  [dim]{unstamped['firings']:,} firings predate build stamping and "
            "cannot be attributed to an install.[/dim]"
        )
    if not stamped:
        return
    if len(stamped) == 1:
        console.print(f"  [dim]emitted by build {stamped[0]['build']}.[/dim]")
        return
    console.print(
        f"  [yellow]{len(stamped)} builds emitted into this ledger[/yellow] — the rows "
        "above are not one population:"
    )
    for row in stamped:
        console.print(
            f"    [dim]{row['build']}: {row['firings']:,} firings across "
            f"{row['sessions']:,} sessions[/dim]"
        )


@hook_group.command("stats")
@click.argument("path", required=False, default=None)
@format_option(help="Output format. ``json`` emits the raw per-surface rows.")
@json_option()
def hook_stats(path: str | None, fmt: str, as_json: bool) -> None:
    """Show what the agent hooks fired and whether the agent acted on it.

    Reads the efficacy ledger in .repowise/sessions/sessions.db. The live
    hooks write a row the moment they fire; `repowise hook backfill` (and the
    update-time pass) replay transcripts to settle whether each firing was
    acted on. A surface showing firings but no verdicts has not been
    classified yet — run the backfill.
    """
    from repowise.core.sessions.efficacy import (
        CLASSIFIED_SURFACES,
        NO_ACTION_EXPECTED,
        RETIRED_CATEGORIES,
    )
    from repowise.core.sessions.staging import SessionStagingStore, default_store_path

    fmt = resolve_format(fmt, as_json)
    notices = notice_console(fmt)

    target = resolve_command_target(path=path, workspace_flag=False, no_workspace_flag=True)
    assert target.repo_path is not None
    if not default_store_path(target.repo_path).exists():
        notices.print("[yellow]No hook ledger yet.[/yellow] Run `repowise hook backfill`.")
        if fmt == "json":
            emit_json(_EMPTY_STATS)
        return

    store = SessionStagingStore.open_default(target.repo_path)
    try:
        rows = store.efficacy_rows()
        session_totals = store.session_duration_totals()
        runs = store.hook_run_totals()
        by_tool = store.hook_run_by_tool()
        feedback = store.decision_feedback_totals()
        builds = store.injection_builds()
        rewrites = store.rewrite_run_totals()
    finally:
        store.close()
    if not rows:
        notices.print("[yellow]Hook ledger is empty.[/yellow] Run `repowise hook backfill`.")
        if fmt == "json":
            emit_json(
                {
                    **_EMPTY_STATS,
                    "runs": by_tool,
                    "decision_feedback": feedback,
                    "builds": builds,
                    "rewrite": rewrites,
                }
            )
        return

    if fmt == "json":
        # The machine-readable twin carries the same lie the table did, so it
        # gets the same label rather than a footer nothing can parse.
        for row in rows:
            row["retired"] = (row["surface"], row["category"]) in RETIRED_CATEGORIES
        emit_json(
            {
                "surfaces": rows,
                "runs": by_tool,
                "decision_feedback": feedback,
                "builds": builds,
                "rewrite": rewrites,
            }
        )
        return

    from rich.table import Table

    table = Table(title="Agent hook efficacy", header_style="bold")
    table.add_column("surface/category")
    table.add_column("fired", justify="right")
    table.add_column("sessions", justify="right")
    table.add_column("acted", justify="right")
    table.add_column("rate", justify="right")
    table.add_column("cost", justify="right")
    table.add_column("median ms", justify="right")

    for row in rows:
        pair = (row["surface"], row["category"])
        classified = row["evaluated"]
        retired = pair in RETIRED_CATEGORIES
        if retired:
            # A closed population. Its firings and cost are real history and
            # stay visible; its rate is not a rate, because the denominator
            # stopped growing when the emission was deleted.
            acted, rate = "-", "[dim]retired[/dim]"
        elif row["surface"] not in CLASSIFIED_SURFACES:
            # Decision rows are judged as followed-vs-contradicted against the
            # decision records, not as acted-on; read_enrich never emits.
            acted, rate = "-", "[dim]n/a[/dim]"
        elif pair in NO_ACTION_EXPECTED:
            acted, rate = "-", "[dim]n/a[/dim]"
        elif not classified:
            acted, rate = "-", "[dim]unclassified[/dim]"
        else:
            acted = str(row["acted"])
            pct = 100.0 * row["acted"] / classified
            colour = "green" if pct >= 20 else ("yellow" if pct >= 5 else "red")
            rate = f"[{colour}]{pct:.1f}%[/{colour}]"
        n = row["duration_ms_count"]
        # Dimming the whole row is what makes a retired surface legible at a
        # glance; the rate cell alone is too easy to read past.
        lo, hi = ("[dim]", "[/dim]") if retired else ("", "")
        label = f"{row['surface']}/{row['category']}" if row["category"] else row["surface"]
        table.add_row(
            f"{lo}{label}{hi}",
            f"{lo}{row['firings']}{hi}",
            f"{lo}{row['sessions']}{hi}",
            acted if acted == "-" else f"{lo}{acted}{hi}",
            rate,
            f"{lo}~{row['chars'] // 4}t{hi}",
            f"{lo}{row['duration_ms_total'] // n}{hi}" if n else "[dim]-[/dim]",
        )
    console.print(table)
    console.print(
        "  [dim]rate is over classified firings only, and 'n/a' marks a notice "
        "with no action to take. Dimmed 'retired' rows are history: the "
        "emission is gone, so the counts cannot grow and the rate is not "
        "adoption.[/dim]"
    )

    _print_rewrite(rewrites)
    _print_builds(builds)

    # The decision surface's own verdict, which the acted-on rate above cannot
    # carry: an injected decision is judged by whether the session went on to
    # contradict it, not by whether a tool call followed it.
    if any(feedback.values()):
        judged = feedback["followed"] + feedback["contradicted"]
        parts = [
            f"[green]{feedback['followed']} followed[/green]",
            f"[yellow]{feedback['contradicted']} contradicted[/yellow]",
        ]
        if feedback["pending"]:
            parts.append(f"[dim]{feedback['pending']} awaiting the next update[/dim]")
        if feedback["no_verdict"]:
            parts.append(
                f"[dim]{feedback['no_verdict']} unjudged, mostly for want of "
                "anything to judge them against[/dim]"
            )
        console.print(f"  injected decisions: {', '.join(parts)}")
        if judged:
            pct = 100.0 * feedback["followed"] / judged
            console.print(
                f"  [dim]{pct:.0f}% of judged injections were followed. Judged means the "
                "session mined a correction that could have disagreed; the rest are "
                "counted nowhere.[/dim]"
            )

    if session_totals:
        session_totals.sort()
        median = session_totals[len(session_totals) // 2]
        worst = session_totals[-1]
        console.print(
            f"  [dim]emitting firings cost, per session: median {median / 1000:.1f}s, "
            f"worst {worst / 1000:.1f}s across {len(session_totals)} sessions.[/dim]"
        )

    if not runs:
        console.print(
            "\n  [dim]No invocation counts yet — silent hook runs are only recorded "
            "going forward, and they are most of the cost. Re-check after a session "
            "or two.[/dim]"
        )
        return

    calls = sorted(r["calls"] for r in runs)
    spent = sorted(r["total_ms"] for r in runs)
    total_calls = sum(calls)
    total_emitted = sum(r["emitted"] for r in runs)
    console.print(
        f"\n[bold]Hook invocations[/bold] across {len(runs)} session(s): "
        f"{total_calls} calls, {total_emitted} of them said something "
        f"({100.0 * total_emitted / total_calls:.0f}%)."
    )
    console.print(
        f"  per session: median {calls[len(calls) // 2]} calls / "
        f"{spent[len(spent) // 2] / 1000:.1f}s, worst {calls[-1]} calls / "
        f"{spent[-1] / 1000:.1f}s"
    )
    console.print(
        "  [dim]in-process time only: the interpreter start before repowise loads "
        "is not counted, so this is a floor.[/dim]"
    )
    for row in by_tool[:6]:
        label = f"{row['event']}:{row['tool']}" if row["tool"] else row["event"]
        console.print(
            f"    {label:28} {row['calls']:5} calls  "
            f"{row['total_ms'] / 1000:7.1f}s  "
            f"[dim]{row['calls'] - row['emitted']} silent[/dim]"
        )


@hook_group.command("backfill")
@click.argument("path", required=False, default=None)
@click.option(
    "--all-projects",
    is_flag=True,
    default=False,
    help="Also replay transcripts from this repo's worktrees, not just this checkout.",
)
@click.option(
    "--days",
    type=int,
    default=None,
    help="Only replay transcripts modified in the last N days (default: all history).",
)
@click.option(
    "--reset",
    is_flag=True,
    default=False,
    help=(
        "Clear every transcript-classified surface's rows before replaying. "
        "Run this once after upgrading: rows written under the older ledger "
        "keys cannot be matched to a transcript firing and would be counted "
        "twice. Decision rows are never touched."
    ),
)
def hook_backfill(path: str | None, all_projects: bool, days: int | None, reset: bool) -> None:
    """Replay agent transcripts into the hook efficacy ledger.

    Every firing is keyed by a hash of its own text, so this is safe to re-run:
    a replayed firing settles the row it already owns instead of adding a new
    one. Scanning is single-pass and local; nothing leaves the machine.
    """
    import time

    from repowise.core.sessions.efficacy import discover_transcripts, ingest_transcript_efficacy

    target = resolve_command_target(path=path, workspace_flag=False, no_workspace_flag=True)
    assert target.repo_path is not None

    found = discover_transcripts(target.repo_path, all_projects=all_projects)
    if not found:
        console.print(
            "[yellow]No Claude Code transcripts found for this repo.[/yellow] "
            "Hook efficacy is measured from them, so there is nothing to backfill."
        )
        return

    since = time.time() - days * 86400.0 if days else None
    console.print(f"Replaying {len(found)} transcript(s)...")
    counts = ingest_transcript_efficacy(
        target.repo_path, all_projects=all_projects, since=since, reset=reset
    )
    if not counts:
        console.print("  [dim]No repowise hook firings in range.[/dim]")
        return
    for key in sorted(counts, key=lambda k: -counts[k]):
        console.print(f"  {key:28} {counts[key]}")
    console.print("\nRun `repowise hook stats` to see action rates.")


@hook_group.command("status")
@click.argument("path", required=False, default=None)
@click.option(
    "--workspace",
    "-w",
    is_flag=True,
    default=False,
    help="Force workspace mode (report hooks for every repo in the workspace).",
)
@click.option(
    "--no-workspace",
    is_flag=True,
    default=False,
    help="Force single-repo mode even when invoked from a workspace.",
)
def hook_status(path: str | None, workspace: bool, no_workspace: bool) -> None:
    """Check if the repowise post-commit hook is installed."""
    from repowise.cli.hooks import status

    target = _hook_target(path, workspace, no_workspace)

    if target.is_workspace:
        assert target.ws_root is not None and target.ws_config is not None
        for entry in target.ws_config.repos:
            abs_path = (target.ws_root / entry.path).resolve()
            result = status(abs_path)
            icon = "[green]✓[/green]" if result == "installed" else "[dim]✗[/dim]"
            console.print(f"  {icon} {entry.alias}: {result}")
    else:
        assert target.repo_path is not None
        result = status(target.repo_path)
        icon = "[green]✓[/green]" if result == "installed" else "[dim]✗[/dim]"
        console.print(f"  {icon} post-commit: {result}")
