"""Workspace: interactive repo + primary selection."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from repowise.cli.ui.brand import BRAND_STYLE, OK, WARN


def _display_path(repo: Any, root: Any) -> str:
    """Where this repo sits, relative to the workspace root.

    ``repo.path.name`` is the leaf directory, which for almost every repo is
    the string already in the Repository column — and on a workspace with two
    ``api`` directories under different parents, the one column that would tell
    them apart showed the same text twice.
    """
    if root is None:
        return repo.path.name
    try:
        return repo.path.relative_to(root).as_posix()
    except ValueError:
        # Outside the root (a symlinked or explicitly added repo).
        return str(repo.path)


def interactive_repo_select(
    console: Console,
    repos: list[Any],
    root: Any = None,
) -> list[Any]:
    """Display discovered repos and let the user pick which ones to index.

    *repos* is a list of :class:`~repowise.core.workspace.scanner.DiscoveredRepo`.
    *root* is the workspace root, used to render each repo's path relative to it.
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
        table.add_row(f"[{idx}]", repo.name, _display_path(repo, root), status)

    console.print()
    console.print(table)
    console.print()
    # Ranges and 'none' are supported; they used to be discoverable only by
    # typing something invalid first.
    console.print("  [dim]Numbers (1,2,3), ranges (1-3), 'all' or 'none'.[/dim]")

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

        selected = parse_selection(raw, len(repos))
        if selected is not None:
            return [repos[i] for i in selected]

        console.print(
            f"  [{WARN}]Invalid selection. Use numbers (1,2,3), ranges (1-3), 'all', or 'none'.[/]"
        )


def parse_selection(raw: str, count: int) -> list[int] | None:
    """Parse a comma-separated selection string into zero-based indices.

    Supports: ``"1,2,3"``, ``"1-3"``, ``"1,3-5"``, ``"1-3,5"``.
    Returns ``None`` on invalid input.

    Shared with the agent multiselect, which uses the same numbers-and-ranges
    grammar. One parser means one set of accepted spellings across the CLI's
    list prompts rather than two that drift.
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
    console.print("  [bold]Primary repository[/bold]")
    console.print(
        "  [dim]The primary repo is what MCP tools and the dashboard open by default.\n"
        "  You can change it later in repowise.workspace.yaml.[/dim]"
    )
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
