"""The ``repowise workspace`` group and its config-editing subcommands."""

from __future__ import annotations

from pathlib import Path

import click
from rich.table import Table

from repowise.cli._setup import configure_cli_logging
from repowise.cli.commands.status_cmd import _format_relative_time, _query_repo_counts
from repowise.cli.helpers import console, resolve_repo_path

from ._index import _resolve_docs_flag, _run_index_for_repo
from ._shared import _require_workspace

# ---------------------------------------------------------------------------
# Command group
# ---------------------------------------------------------------------------


@click.group("workspace")
def workspace_group() -> None:
    """Manage multi-repo workspaces."""


# ---------------------------------------------------------------------------
# workspace list
# ---------------------------------------------------------------------------


@workspace_group.command("list")
@click.argument("path", required=False, default=None)
def workspace_list(path: str | None) -> None:
    """Show all repos in the workspace with their status."""
    from repowise.cli.helpers import get_repowise_dir
    from repowise.core.workspace import check_repo_staleness

    start = resolve_repo_path(path)
    ws_root, ws_config = _require_workspace(start)

    table = Table(title=f"Workspace: {ws_root.name}")
    table.add_column("Repo", style="cyan", min_width=16)
    table.add_column("Path", style="dim")
    table.add_column("Files", justify="right")
    table.add_column("Symbols", justify="right")
    table.add_column("Indexed", style="dim")
    table.add_column("Status")

    indexed_count = 0

    for entry in ws_config.repos:
        abs_path = (ws_root / entry.path).resolve()
        repowise_dir = get_repowise_dir(abs_path)

        label = entry.alias
        if entry.alias == ws_config.default_repo:
            label += " [bold](primary)[/bold]"

        rel_path = entry.path

        if not repowise_dir.exists():
            table.add_row(label, rel_path, "-", "-", "-", "[yellow]not indexed[/yellow]")
            continue

        indexed_count += 1

        # Query file/symbol counts from DB
        file_count, symbol_count = _query_repo_counts(abs_path)

        # Indexed timestamp
        indexed_ago = _format_relative_time(entry.indexed_at)

        # Staleness check
        is_stale, _head, behind = check_repo_staleness(abs_path, entry.last_commit_at_index)

        if is_stale and behind > 0:
            status = f"[yellow]{behind} new commit(s)[/yellow]"
        elif is_stale:
            status = "[yellow]stale[/yellow]"
        elif file_count > 0:
            status = "[green]up to date[/green]"
        else:
            status = "[yellow]empty[/yellow]"

        table.add_row(
            label,
            rel_path,
            str(file_count),
            f"{symbol_count:,}",
            indexed_ago,
            status,
        )

    console.print(table)

    total_repos = len(ws_config.repos)
    summary = f"\n  {indexed_count}/{total_repos} repos indexed."
    if ws_config.default_repo:
        summary += f" Default: {ws_config.default_repo}"
    console.print(summary)


# ---------------------------------------------------------------------------
# workspace add
# ---------------------------------------------------------------------------


@workspace_group.command("add")
@click.argument("path")
@click.option("--alias", default=None, help="Short name for the repo (default: directory name).")
@click.option(
    "--index/--no-index",
    "run_index",
    default=True,
    show_default=True,
    help="Run full indexing on the repo after adding it (graph, git, dead code).",
)
@click.option(
    "--docs/--no-docs",
    "run_docs",
    default=None,
    help=(
        "--docs writes the documentation with a model (the primary repo's "
        "provider, or --provider). --no-docs indexes only. Default: documentation "
        "built from code, with no model calls and no key. Ignored with --no-index."
    ),
)
@click.option(
    "--provider", "provider_name", default=None, help="LLM provider name (overrides primary's)."
)
@click.option("--model", default=None, help="Model identifier (overrides primary's).")
@click.option(
    "--concurrency", type=int, default=10, help="Max concurrent LLM calls during doc generation."
)
@click.option(
    "--save-key/--no-save-key",
    "save_key",
    default=True,
    help=(
        "Save the provider API key this run authenticated with into the added "
        "repo's .repowise/.env (gitignored). Default: on, because each workspace repo "
        "has its own .repowise/, so each needs its own key to be answerable by "
        "the MCP server. Same switch as `repowise init --no-save-key`."
    ),
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Show debug logs from the pipeline.",
)
def workspace_add(
    path: str,
    alias: str | None,
    run_index: bool,
    run_docs: bool | None,
    provider_name: str | None,
    model: str | None,
    concurrency: int,
    save_key: bool,
    verbose: bool,
) -> None:
    """Add a repo to the workspace, index it and build its documentation.

    PATH is a relative or absolute path to a git repository.

    By default the repo is indexed and its documentation is built from code,
    with no model calls. Pass --docs (or --provider) to have a model write it,
    --no-docs to index only, or --no-index to only register the entry.
    """
    configure_cli_logging(verbose=verbose)

    from repowise.core.workspace.config import RepoEntry

    repo_path = Path(path).resolve()
    ws_root, ws_config = _require_workspace(Path.cwd())

    # Validate path exists
    if not repo_path.exists():
        raise click.ClickException(f"Path does not exist: {repo_path}")

    # Validate it is a git repo
    if not (repo_path / ".git").exists():
        raise click.ClickException(f"Not a git repository (no .git found): {repo_path}")

    # Default alias to directory name
    if alias is None:
        alias = repo_path.name.lower()

    # Validate alias is not already in workspace
    if ws_config.get_repo(alias) is not None:
        raise click.ClickException(
            f"Alias '{alias}' already exists in this workspace. "
            "Use --alias to specify a different name."
        )

    # Build a relative path from ws_root
    try:
        rel_path = repo_path.relative_to(ws_root).as_posix()
    except ValueError:
        # Repo is outside workspace root — store absolute path as-is
        rel_path = repo_path.as_posix()

    entry = RepoEntry(path=rel_path, alias=alias)

    try:
        ws_config.add_repo(entry)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    ws_config.save(ws_root)
    console.print(f"[green]✓[/green] Added repo '{alias}' ({rel_path}) to workspace.")

    if not run_index:
        console.print(
            "[yellow]Skipping index[/yellow] (--no-index). "
            f"Run [bold]repowise update --repo {alias}[/bold] to index later."
        )
        return

    # Resolve whether docs should run.
    docs_mode, docs_skip_reason = _resolve_docs_flag(
        run_docs=run_docs, provider_name=provider_name
    )

    _run_index_for_repo(
        repo_path,
        alias,
        ws_root,
        ws_config,
        docs_mode=docs_mode,
        provider_name=provider_name,
        model=model,
        concurrency=concurrency,
        docs_skip_reason=docs_skip_reason,
        save_key=save_key,
    )


# ---------------------------------------------------------------------------
# workspace remove
# ---------------------------------------------------------------------------


@workspace_group.command("remove")
@click.argument("alias")
def workspace_remove(alias: str) -> None:
    """Remove a repo from the workspace config.

    The repo's .repowise/ directory is preserved; only the workspace
    entry is deleted.
    """
    ws_root, ws_config = _require_workspace(Path.cwd())

    entry = ws_config.get_repo(alias)
    if entry is None:
        available = ", ".join(ws_config.repo_aliases()) or "(none)"
        raise click.ClickException(f"No repo with alias '{alias}' found. Available: {available}")

    is_default = alias == ws_config.default_repo

    removed = ws_config.remove_repo(alias)
    if removed is None:
        raise click.ClickException(f"Failed to remove repo '{alias}'.")

    ws_config.save(ws_root)
    console.print(f"[green]✓[/green] Removed repo '{alias}' from workspace.")

    if is_default and ws_config.repos:
        new_default = ws_config.repos[0].alias
        console.print(
            f"[yellow]Note:[/yellow] '{alias}' was the default repo. "
            f"New default is '{new_default}'."
        )
    elif is_default:
        console.print("[yellow]Note:[/yellow] Workspace now has no repos and no default.")

    console.print(f"  (Indexed data at {removed.path}/.repowise/ was [bold]not[/bold] deleted.)")


# ---------------------------------------------------------------------------
# workspace scan
# ---------------------------------------------------------------------------


@workspace_group.command("scan")
@click.argument("path", required=False, default=None)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Auto-add all discovered repos without prompting.",
)
@click.option(
    "--exclude",
    "exclude_globs",
    multiple=True,
    metavar="GLOB",
    help=(
        "Skip discovered repos whose path matches this glob, relative to the "
        "workspace root (e.g. 'test-repos/*'). Repeatable."
    ),
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Show debug logs from the pipeline.",
)
def workspace_scan(
    path: str | None, yes: bool, exclude_globs: tuple[str, ...], verbose: bool
) -> None:
    """Scan the workspace root for new repos not yet in the config."""
    configure_cli_logging(verbose=verbose)

    from repowise.core.workspace.config import RepoEntry
    from repowise.core.workspace.scanner import scan_for_repos

    start = resolve_repo_path(path)
    ws_root, ws_config = _require_workspace(start)

    console.print(f"Scanning [cyan]{ws_root}[/cyan] for git repositories…")
    scan_result = scan_for_repos(ws_root)

    existing_aliases = set(ws_config.repo_aliases())
    existing_paths = {(ws_root / e.path).resolve().as_posix() for e in ws_config.repos}

    new_repos = [
        r
        for r in scan_result.repos
        if r.path.as_posix() not in existing_paths and r.alias not in existing_aliases
    ]

    if exclude_globs:
        from fnmatch import fnmatchcase

        def _excluded(repo_path: Path) -> bool:
            try:
                rel = repo_path.relative_to(ws_root).as_posix()
            except ValueError:
                rel = repo_path.as_posix()
            return any(fnmatchcase(rel, g) for g in exclude_globs)

        before = len(new_repos)
        new_repos = [r for r in new_repos if not _excluded(r.path)]
        skipped = before - len(new_repos)
        if skipped:
            console.print(f"[dim]Excluded {skipped} repo(s) by --exclude.[/dim]")

    if not new_repos:
        console.print("[green]No new repositories discovered.[/green]")
        return

    console.print(f"\nFound [bold]{len(new_repos)}[/bold] new repo(s) not in workspace:\n")
    for repo in new_repos:
        indexed_marker = " [green](indexed)[/green]" if repo.has_repowise else ""
        console.print(f"  [cyan]{repo.alias}[/cyan] — {repo.path}{indexed_marker}")

    console.print()

    # A scan of a directory full of fixtures can find a hundred repos, and
    # prompting per repo with no way out but Ctrl-C is not a choice. Ask once
    # before starting, so declining everything costs one keystroke.
    if not yes and not click.confirm(
        f"Review these {len(new_repos)} repo(s) one at a time?", default=True
    ):
        console.print(
            "\nNo repos added. Narrow the scan with [bold]--exclude 'dir/*'[/bold] "
            "or add all with [bold]--yes[/bold]."
        )
        return

    added = 0
    for repo in new_repos:
        alias = repo.alias

        # Resolve alias collisions
        base_alias = alias
        suffix = 2
        while ws_config.get_repo(alias) is not None:
            alias = f"{base_alias}-{suffix}"
            suffix += 1

        if yes:
            do_add = True
        else:
            do_add = click.confirm(f"Add '{alias}' ({repo.path.relative_to(ws_root)})?")

        if do_add:
            try:
                rel_path = repo.path.relative_to(ws_root).as_posix()
            except ValueError:
                rel_path = repo.path.as_posix()

            entry = RepoEntry(path=rel_path, alias=alias)
            ws_config.add_repo(entry)
            console.print(f"  [green]✓[/green] Added '{alias}'.")
            added += 1

    if added > 0:
        ws_config.save(ws_root)
        console.print(f"\n[green]{added} repo(s) added to workspace.[/green]")
    else:
        console.print("\nNo repos added.")


# ---------------------------------------------------------------------------
# workspace set-default
# ---------------------------------------------------------------------------


@workspace_group.command("set-default")
@click.argument("alias")
def workspace_set_default(alias: str) -> None:
    """Change the default (primary) repo in the workspace."""
    ws_root, ws_config = _require_workspace(Path.cwd())

    entry = ws_config.get_repo(alias)
    if entry is None:
        available = ", ".join(ws_config.repo_aliases()) or "(none)"
        raise click.ClickException(f"No repo with alias '{alias}' found. Available: {available}")

    previous_default = ws_config.default_repo

    # Update is_primary flags on all entries
    for repo_entry in ws_config.repos:
        repo_entry.is_primary = repo_entry.alias == alias

    ws_config.default_repo = alias
    ws_config.save(ws_root)

    if previous_default and previous_default != alias:
        console.print(
            f"[green]✓[/green] Default repo changed from "
            f"'[dim]{previous_default}[/dim]' to '[bold]{alias}[/bold]'."
        )
    else:
        console.print(f"[green]✓[/green] Default repo set to '[bold]{alias}[/bold]'.")
