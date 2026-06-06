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
    """Manage the distill command-rewrite hook (Claude Code PreToolUse).

    When installed, noisy commands an agent runs (tests, builds, git
    status/log/diff, searches, listings) are rewritten to
    ``repowise distill <command>`` — pending your approval — so the agent
    sees a compact, errors-first rendering. Raw output stays recoverable
    via ``repowise expand <ref>``.
    """


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
        "commands so existing allowlist entries keep working after rewrites. "
        "Prompts when omitted (interactive only)."
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
    import sys

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
        "in .repowise/config.yaml (permission: ask | allow).[/dim]"
    )

    # A rewrite changes the command string, so a user's existing allowlist
    # entry (e.g. `Bash(git diff:*)`) no longer matches the rewritten
    # `repowise distill git diff …` — every rewrite asks again. Offer to
    # seed an allow rule for the distill prefix; strictly opt-in, the hook
    # posture itself stays `ask`.
    if allow_rule is None and sys.stdin.isatty():
        console.print(
            "  [dim]Rewritten commands no longer match your existing Claude Code "
            "allowlist entries (e.g. `Bash(git diff:*)`), so they prompt again.[/dim]"
        )
        allow_rule = click.confirm(
            "  Add a permission allow rule for `repowise distill` commands?",
            default=False,
        )
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


@rewrite_group.command("uninstall")
def rewrite_uninstall() -> None:
    """Remove the rewrite hook from ~/.claude/settings.json."""
    from repowise.cli.agent_adapters.claude_code import ClaudeCodeAdapter

    removed = ClaudeCodeAdapter().uninstall_rewrite_hook()
    console.print(f"Rewrite hook: {'[green]removed[/green]' if removed else 'not installed'}")


@rewrite_group.command("status")
def rewrite_status() -> None:
    """Check whether the rewrite hook is installed."""
    from repowise.cli.agent_adapters.claude_code import ClaudeCodeAdapter

    installed = ClaudeCodeAdapter().rewrite_hook_installed()
    icon = "[green]✓[/green]" if installed else "[dim]✗[/dim]"
    console.print(
        f"  {icon} claude-code rewrite hook: {'installed' if installed else 'not installed'}"
    )


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
