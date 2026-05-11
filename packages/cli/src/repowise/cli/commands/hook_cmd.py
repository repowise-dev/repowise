"""``repowise hook`` — manage git post-commit hooks for auto-sync."""

from __future__ import annotations

import click

from repowise.cli.helpers import (
    console,
    find_workspace_root,
    resolve_command_target,
    resolve_repo_path,
)


@click.group("hook")
def hook_group() -> None:
    """Manage git hooks for automatic wiki sync."""


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
