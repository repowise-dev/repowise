"""Which repos a multi-repo ``repowise init`` indexes, and which one is primary."""

from __future__ import annotations

import sys
from typing import Any

import click

from repowise.cli.helpers import console
from repowise.cli.ui.brand import WARN
from repowise.cli.ui.workspace_selection import (
    interactive_primary_select,
    interactive_repo_select,
    match_repo_names,
)

from ._workspace_config import WorkspacePick

#: Names shown in the all-repos line before it says "and N more".
_NAMES_SHOWN = 10


def split_repo_names(raw: str | None) -> list[str]:
    """``--repos a,b , c`` as ``["a", "b", "c"]``; empty when the flag is absent."""
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def check_repos_flag(repos: str | None, *, init_all: bool, is_workspace: bool) -> None:
    """Reject ``--repos`` where it cannot apply, before any indexing starts."""
    if repos is None:
        return
    # An empty value would otherwise fall through to indexing every repo.
    if not split_repo_names(repos):
        raise click.UsageError("--repos needs at least one repo name.")
    if init_all:
        raise click.UsageError("--repos and --all contradict each other. Pass one.")
    if not is_workspace:
        raise click.UsageError(
            "--repos picks repos in a folder that holds several; this run indexes one repo."
        )


def select_workspace_repos(
    scan: Any, *, repo_names: list[str], select_all: bool
) -> WorkspacePick | None:
    """The repos to index and the primary; None when none were selected.

    ``--repos`` wins over everything and never prompts. Otherwise ``--yes``,
    ``--all`` or a non-terminal stdin take every detected repo (the first is
    primary): asking a pipe would end the run with an EOFError before anything
    was done.
    """
    root = scan.root
    if repo_names:
        try:
            selected = match_repo_names(scan.repos, repo_names, root)
        except ValueError as exc:
            raise click.UsageError(f"--repos: {exc}") from exc
        # The first name listed is primary only for a new workspace; an existing
        # workspace keeps its primary (see repos_primary_line).
        return WorkspacePick(selected, root, selected[0].alias, selected[0], False)

    select_all = select_all or not sys.stdin.isatty()
    if select_all:
        selected = list(scan.repos)
        console.print(all_repos_line(selected))
    else:
        selected = interactive_repo_select(console, scan.repos, root)
    if not selected:
        console.print(f"[{WARN}]No repositories selected. Aborting.[/]")
        return None
    primary_alias = (
        selected[0].alias if select_all else interactive_primary_select(console, selected)
    )
    primary_repo = next((r for r in selected if r.alias == primary_alias), selected[0])
    # interactive_primary_select asks only when there is more than one to choose from.
    primary_picked = not select_all and len(selected) > 1
    return WorkspacePick(selected, root, primary_alias, primary_repo, primary_picked)


def repos_primary_line(default_repo: str | None, pick: WorkspacePick) -> str:
    """The primary a ``--repos`` run ended with, and where it came from."""
    if default_repo and default_repo != pick.primary_alias:
        return f"  Primary: [bold]{default_repo}[/bold] (kept from the workspace)\n"
    return f"  Primary: [bold]{pick.primary_alias}[/bold] (first in --repos)\n"


def all_repos_line(repos: list[Any]) -> str:
    """One line naming every repo a promptless run takes, and how to pick fewer."""
    names = [r.alias for r in repos]
    shown = ", ".join(names[:_NAMES_SHOWN])
    if len(names) > _NAMES_SHOWN:
        shown += f" and {len(names) - _NAMES_SHOWN} more"
    example = ",".join(names[:2])
    return (
        f"  Indexing all {len(names)} repos: {shown}.\n"
        f"  [dim]Pick some with --repos {example}[/dim]\n"
    )
