"""``repowise hook`` — manage git post-commit hooks and agent hooks."""

from __future__ import annotations

import click

from repowise.cli.helpers import (
    console,
    resolve_command_target,
)


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
    ``repowise distill <command>`` — pending your approval — so the agent
    sees a compact, errors-first rendering. Raw output stays recoverable
    via ``repowise expand <ref>``.

    Claude Code gets the full ask-posture rewrite. Codex hooks cannot show
    a rewritten command for approval, so there rewrites apply only to
    command families set to ``permission: allow``; every Codex install also
    maintains an AGENTS.md awareness section that works without any hook.
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

    installed = ClaudeCodeAdapter().rewrite_hook_installed()
    icon = "[green]✓[/green]" if installed else "[dim]✗[/dim]"
    console.print(
        f"  {icon} claude-code rewrite hook: {'installed' if installed else 'not installed'}"
    )

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
    codex_installed = codex.rewrite_hook_installed()
    icon = "[green]✓[/green]" if codex_installed else "[dim]✗[/dim]"
    console.print(
        f"  {icon} codex rewrite hook: {'installed' if codex_installed else 'not installed'}"
    )
    console.print(f"      [dim]{_codex_capability_note(version, supports)}[/dim]")

    target = _hook_target(path, workspace, no_workspace)
    for repo_path in _target_repo_paths(target):
        section = agents_md_distill_section_installed(repo_path)
        icon = "[green]✓[/green]" if section else "[dim]✗[/dim]"
        state = "installed" if section else "not installed"
        console.print(f"  {icon} AGENTS.md distill section: {state} ({repo_path})")


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
