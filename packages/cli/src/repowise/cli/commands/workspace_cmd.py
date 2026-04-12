"""``repowise workspace`` — manage multi-repo workspaces."""

from __future__ import annotations

from pathlib import Path

import click
from rich.table import Table

from repowise.cli.helpers import (
    console,
    find_workspace_root,
    resolve_repo_path,
    run_async,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_workspace(start: Path | None = None) -> tuple[Path, "WorkspaceConfig"]:  # type: ignore[name-defined]
    """Load the workspace config or abort with a helpful message.

    Returns ``(ws_root, ws_config)``.
    """
    from repowise.core.workspace.config import WorkspaceConfig

    ws_root = find_workspace_root(start)
    if ws_root is None:
        raise click.ClickException(
            "No .repowise-workspace.yaml found. "
            "Run 'repowise init <workspace-dir>' to create a workspace."
        )
    ws_config = WorkspaceConfig.load(ws_root)
    return ws_root, ws_config


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


def _query_repo_counts(repo_path: Path) -> tuple[int, int]:
    """Return ``(file_count, symbol_count)`` from a repo's DB, or ``(0, 0)``."""
    from repowise.cli.helpers import get_db_url_for_repo, get_repowise_dir

    db_path = get_repowise_dir(repo_path) / "wiki.db"
    if not db_path.exists():
        return 0, 0

    async def _query() -> tuple[int, int]:
        from sqlalchemy import func as sa_func
        from sqlalchemy import select as sa_select

        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
        )
        from repowise.core.persistence.models import GraphNode, Repository

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        sf = create_session_factory(engine)
        try:
            async with get_session(sf) as session:
                repo_result = await session.execute(
                    sa_select(Repository.id).where(Repository.local_path == str(repo_path))
                )
                repo_id = repo_result.scalar_one_or_none()
                if repo_id is None:
                    return 0, 0
                file_result = await session.execute(
                    sa_select(sa_func.count())
                    .select_from(GraphNode)
                    .where(
                        GraphNode.repository_id == repo_id,
                        GraphNode.node_type == "file",
                    )
                )
                symbol_result = await session.execute(
                    sa_select(sa_func.count())
                    .select_from(GraphNode)
                    .where(
                        GraphNode.repository_id == repo_id,
                        GraphNode.node_type == "symbol",
                    )
                )
                return file_result.scalar_one(), symbol_result.scalar_one()
        finally:
            await engine.dispose()

    try:
        return run_async(_query())
    except Exception:
        return 0, 0


def _format_relative_time(iso_timestamp: str | None) -> str:
    """Format an ISO 8601 timestamp as a human-readable relative string."""
    if not iso_timestamp:
        return "-"
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(iso_timestamp)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - dt
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return f"{seconds}s ago"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        return f"{seconds // 86400}d ago"
    except Exception:
        return iso_timestamp[:10] if len(iso_timestamp) >= 10 else iso_timestamp


# ---------------------------------------------------------------------------
# workspace add
# ---------------------------------------------------------------------------


@workspace_group.command("add")
@click.argument("path")
@click.option("--alias", default=None, help="Short name for the repo (default: directory name).")
@click.option(
    "--index",
    "run_index",
    is_flag=True,
    default=False,
    help="Run indexing on the repo after adding it.",
)
def workspace_add(path: str, alias: str | None, run_index: bool) -> None:
    """Add a repo to the workspace.

    PATH is a relative or absolute path to a git repository.
    """
    from repowise.core.workspace.config import RepoEntry

    repo_path = Path(path).resolve()
    ws_root, ws_config = _require_workspace(Path.cwd())

    # Validate path exists
    if not repo_path.exists():
        raise click.ClickException(f"Path does not exist: {repo_path}")

    # Validate it is a git repo
    if not (repo_path / ".git").exists():
        raise click.ClickException(
            f"Not a git repository (no .git found): {repo_path}"
        )

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

    if run_index:
        _run_index_for_repo(repo_path, alias, ws_root, ws_config)


def _run_index_for_repo(
    repo_path: Path,
    alias: str,
    ws_root: Path,
    ws_config: "WorkspaceConfig",  # type: ignore[name-defined]
) -> None:
    """Run the ingestion pipeline on a single repo and update workspace config."""
    from datetime import datetime, timezone

    from repowise.core.pipeline.orchestrator import run_pipeline
    from repowise.core.workspace.update import run_cross_repo_hooks

    console.print(f"  Indexing [cyan]{alias}[/cyan]…")

    async def _do_index() -> None:
        result = await run_pipeline(repo_path, generate_docs=False)

        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            upsert_repository,
        )
        from repowise.core.persistence.database import resolve_db_url
        from repowise.core.pipeline import persist_pipeline_result

        url = resolve_db_url(repo_path)
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await upsert_repository(
                session, name=result.repo_name, local_path=str(repo_path)
            )
            await persist_pipeline_result(result, session, repo.id)
        await engine.dispose()

        # Update workspace entry timestamps
        entry = ws_config.get_repo(alias)
        if entry is not None:
            entry.indexed_at = datetime.now(timezone.utc).isoformat()
            from repowise.cli.helpers import get_head_commit

            entry.last_commit_at_index = get_head_commit(repo_path)
        ws_config.save(ws_root)

        # Cross-repo hooks
        await run_cross_repo_hooks(ws_config, ws_root, [alias])

    try:
        run_async(_do_index())
        console.print(f"[green]✓[/green] Indexed '{alias}' successfully.")
    except Exception as exc:
        console.print(f"[yellow]Warning:[/yellow] Indexing failed for '{alias}': {exc}")


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
        raise click.ClickException(
            f"No repo with alias '{alias}' found. Available: {available}"
        )

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
        console.print(
            "[yellow]Note:[/yellow] Workspace now has no repos and no default."
        )

    console.print(
        f"  (Indexed data at {removed.path}/.repowise/ was [bold]not[/bold] deleted.)"
    )


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
def workspace_scan(path: str | None, yes: bool) -> None:
    """Scan the workspace root for new repos not yet in the config."""
    from repowise.core.workspace.config import RepoEntry
    from repowise.core.workspace.scanner import scan_for_repos

    start = resolve_repo_path(path)
    ws_root, ws_config = _require_workspace(start)

    console.print(f"Scanning [cyan]{ws_root}[/cyan] for git repositories…")
    scan_result = scan_for_repos(ws_root)

    existing_aliases = set(ws_config.repo_aliases())
    existing_paths = {
        (ws_root / e.path).resolve().as_posix()
        for e in ws_config.repos
    }

    new_repos = [
        r for r in scan_result.repos
        if r.path.as_posix() not in existing_paths
        and r.alias not in existing_aliases
    ]

    if not new_repos:
        console.print("[green]No new repositories discovered.[/green]")
        return

    console.print(f"\nFound [bold]{len(new_repos)}[/bold] new repo(s) not in workspace:\n")
    for repo in new_repos:
        indexed_marker = " [green](indexed)[/green]" if repo.has_repowise else ""
        console.print(f"  [cyan]{repo.alias}[/cyan] — {repo.path}{indexed_marker}")

    console.print()

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
        raise click.ClickException(
            f"No repo with alias '{alias}' found. Available: {available}"
        )

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
