"""Interactive prompts shared by the single-repo and workspace init flows."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click


def offer_distill_rewrite_hook(
    console_obj: Any,
    repo_path: Path,
    flag: bool | None,
) -> None:
    """Opt-in install of the distill command-rewrite hook (Claude Code).

    ``flag`` is the resolved ``--distill-hook/--no-distill-hook`` value:
    True installs without prompting, False skips AND gates this repo off in
    config (so a hook installed globally from another repo stays inert
    here), None prompts when interactive and does nothing otherwise —
    strictly opt-in.
    """
    import os

    if os.environ.get("REPOWISE_SKIP_EDITOR_SETUP", "").strip().lower() not in (
        "",
        "0",
        "false",
        "no",
    ):
        return
    if flag is None and not sys.stdin.isatty():
        return

    from repowise.cli.agent_adapters.claude_code import ClaudeCodeAdapter
    from repowise.cli.helpers import save_distill_commands_enabled as _save_distill_enabled

    adapter = ClaudeCodeAdapter()
    if flag is None:
        if not adapter.detect():
            return
        console_obj.print()
        console_obj.print(
            "[bold]Distill:[/bold] rewrite noisy agent commands (tests, builds, "
            "git, searches) to `repowise distill ...` for compact output?"
        )
        console_obj.print(
            "  [dim]Each rewrite is shown for approval; raw output stays "
            "recoverable via `repowise expand`.[/dim]"
        )
        flag = click.confirm("  Install the Claude Code rewrite hook?", default=False)

    if flag:
        path = adapter.install_rewrite_hook()
        if path:
            console_obj.print(f"  [green]✓[/green] Rewrite hook installed ({path})")
        else:
            console_obj.print("  [yellow]Rewrite hook install failed.[/yellow]")
        _save_distill_enabled(repo_path, enabled=True)
    else:
        console_obj.print(
            "  [dim]Skipped. Run 'repowise hook rewrite install' later to set up.[/dim]"
        )
        _save_distill_enabled(repo_path, enabled=False)


def offer_hook_install(
    console_obj: Any,
    repo_paths: list[Path],
    aliases: list[str] | None = None,
) -> None:
    """Interactively offer to install post-commit hooks for auto-sync.

    For a single repo, asks yes/no.  For multiple repos (workspace), lets the
    user pick which repos to install hooks for.
    """
    if not sys.stdin.isatty():
        return  # Non-interactive — skip

    from repowise.cli.hooks import install, status

    # Filter to repos that don't already have the hook
    candidates: list[tuple[Path, str]] = []
    for i, rp in enumerate(repo_paths):
        label = aliases[i] if aliases else rp.name
        if status(rp) != "installed":
            candidates.append((rp, label))

    if not candidates:
        return  # All already have hooks

    console_obj.print()
    console_obj.print(
        "[bold]Auto-sync:[/bold] Install a post-commit hook to keep the wiki "
        "in sync after every commit?"
    )

    if len(candidates) == 1:
        rp, label = candidates[0]
        if click.confirm(f"  Install post-commit hook for {label}?", default=True):
            result = install(rp)
            console_obj.print(f"  [green]✓[/green] {label}: {result}")
        else:
            console_obj.print("  [dim]Skipped. Run 'repowise hook install' later to set up.[/dim]")
    else:
        # Workspace: show checkboxes-style selection
        console_obj.print("  Select repos (enter numbers, comma-separated, or 'all'):")
        for i, (_rp, label) in enumerate(candidates, 1):
            console_obj.print(f"    [{i}] {label}")

        raw = click.prompt(
            "  Repos",
            default="all",
            show_default=True,
        )
        if raw.strip().lower() == "all":
            selected_indices = list(range(len(candidates)))
        elif raw.strip().lower() in ("none", "skip", ""):
            selected_indices = []
        else:
            try:
                selected_indices = [int(x.strip()) - 1 for x in raw.split(",") if x.strip()]
            except ValueError:
                selected_indices = []

        installed = 0
        for idx in selected_indices:
            if 0 <= idx < len(candidates):
                rp, label = candidates[idx]
                result = install(rp)
                console_obj.print(f"  [green]✓[/green] {label}: {result}")
                installed += 1

        if installed == 0:
            console_obj.print(
                "  [dim]Skipped. Run 'repowise hook install --workspace' later.[/dim]"
            )
