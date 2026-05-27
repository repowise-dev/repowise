"""Workspace: interactive repo + primary selection."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from repowise.cli.ui.brand import BRAND_STYLE, OK, WARN


def interactive_repo_select(
    console: Console,
    repos: list[Any],
) -> list[Any]:
    """Display discovered repos and let the user pick which ones to index.

    *repos* is a list of :class:`~repowise.core.workspace.scanner.DiscoveredRepo`.
    Returns the selected subset in original order.
    """
    # Build display table
    table = Table(
        show_header=True,
        box=None,
        padding=(0, 2),
        title="[bold]Discovered Repositories[/bold]",
        title_style="",
    )
    table.add_column("#", style=BRAND_STYLE, width=4)
    table.add_column("Repository", style="bold", min_width=16)
    table.add_column("Path", style="dim", min_width=20)
    table.add_column("Status", min_width=14)

    for idx, repo in enumerate(repos, 1):
        status = f"[{OK}]indexed[/]" if repo.has_repowise else "[dim]new[/dim]"
        if repo.is_submodule:
            status += " [dim](submodule)[/dim]"
        table.add_row(f"[{idx}]", repo.name, str(repo.path.name), status)

    console.print()
    console.print(table)
    console.print()

    # Selection prompt with retry
    while True:
        raw = Prompt.ask(
            "  Select repos to index",
            default="all",
            console=console,
        )
        raw = raw.strip().lower()

        if raw == "all":
            return list(repos)
        if raw == "none":
            return []

        selected = _parse_selection(raw, len(repos))
        if selected is not None:
            return [repos[i] for i in selected]

        console.print(
            f"  [{WARN}]Invalid selection. Use numbers (1,2,3), ranges (1-3), 'all', or 'none'.[/]"
        )


def _parse_selection(raw: str, count: int) -> list[int] | None:
    """Parse a comma-separated selection string into zero-based indices.

    Supports: ``"1,2,3"``, ``"1-3"``, ``"1,3-5"``, ``"1-3,5"``.
    Returns ``None`` on invalid input.
    """
    indices: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            bounds = part.split("-", 1)
            try:
                lo, hi = int(bounds[0]), int(bounds[1])
            except ValueError:
                return None
            if lo < 1 or hi > count or lo > hi:
                return None
            indices.extend(range(lo - 1, hi))
        else:
            try:
                num = int(part)
            except ValueError:
                return None
            if num < 1 or num > count:
                return None
            indices.append(num - 1)

    if not indices:
        return None

    # Deduplicate while preserving order
    seen: set[int] = set()
    result: list[int] = []
    for i in indices:
        if i not in seen:
            seen.add(i)
            result.append(i)
    return result


def interactive_primary_select(
    console: Console,
    repos: list[Any],
) -> str:
    """Ask which repo is the primary/default. Returns the alias.

    *repos* is the list of selected :class:`DiscoveredRepo` objects.
    """
    if len(repos) == 1:
        return repos[0].alias

    console.print()
    for idx, repo in enumerate(repos, 1):
        console.print(f"  [{BRAND_STYLE}][{idx}][/] {repo.name}")
    console.print()

    choices = [str(i) for i in range(1, len(repos) + 1)]
    chosen = Prompt.ask(
        "  Which is your primary repo?",
        choices=choices,
        default="1",
        console=console,
    )
    return repos[int(chosen) - 1].alias
