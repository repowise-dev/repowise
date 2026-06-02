"""Interactive prompts shared by the single-repo and workspace init flows."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click


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
