"""``repowise status`` — show sync state and page counts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
from rich.table import Table

from repowise.cli.helpers import (
    console,
    find_workspace_root,
    get_db_url_for_repo,
    get_repowise_dir,
    load_state,
    resolve_repo_path,
    run_async,
)


# ---------------------------------------------------------------------------
# Workspace status
# ---------------------------------------------------------------------------


def _query_repo_counts(repo_path: Path) -> tuple[int, int]:
    """Return ``(file_count, symbol_count)`` from a repo's DB."""

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

                # Count file nodes and symbol nodes
                file_count_result = await session.execute(
                    sa_select(sa_func.count())
                    .select_from(GraphNode)
                    .where(
                        GraphNode.repository_id == repo_id,
                        GraphNode.node_type == "file",
                    )
                )
                symbol_count_result = await session.execute(
                    sa_select(sa_func.count())
                    .select_from(GraphNode)
                    .where(
                        GraphNode.repository_id == repo_id,
                        GraphNode.node_type == "symbol",
                    )
                )
                return (
                    file_count_result.scalar_one(),
                    symbol_count_result.scalar_one(),
                )
        finally:
            await engine.dispose()

    db_path = get_repowise_dir(repo_path) / "wiki.db"
    if not db_path.exists():
        return 0, 0
    try:
        return run_async(_query())
    except Exception:
        return 0, 0


def _format_relative_time(iso_timestamp: str | None) -> str:
    """Format an ISO 8601 timestamp as a relative time string."""
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


def _workspace_status(start_path: Path) -> None:
    """Show status for all repos in a workspace."""
    from repowise.cli.helpers import get_head_commit as _get_head
    from repowise.core.workspace import WorkspaceConfig, check_repo_staleness

    ws_root = find_workspace_root(start_path)
    if ws_root is None:
        console.print(
            "[yellow]No .repowise-workspace.yaml found. "
            "Run 'repowise init <workspace-dir>' first.[/yellow]"
        )
        return

    ws_config = WorkspaceConfig.load(ws_root)

    table = Table(title=f"Workspace: {ws_root.name}")
    table.add_column("Repo", style="cyan", min_width=16)
    table.add_column("Files", justify="right")
    table.add_column("Symbols", justify="right")
    table.add_column("Indexed", style="dim")
    table.add_column("HEAD", style="dim")
    table.add_column("Status")

    total_stale = 0

    for entry in ws_config.repos:
        abs_path = (ws_root / entry.path).resolve()
        repowise_dir = abs_path / ".repowise"
        label = entry.alias
        if entry.alias == ws_config.default_repo:
            label += " [bold](primary)[/bold]"

        if not repowise_dir.exists():
            table.add_row(label, "-", "-", "-", "-", "[yellow]not indexed[/yellow]")
            continue

        file_count, symbol_count = _query_repo_counts(abs_path)
        indexed_ago = _format_relative_time(entry.indexed_at)

        # Check staleness by comparing stored commit to current HEAD
        stored_commit = entry.last_commit_at_index
        is_stale, current_head, behind = check_repo_staleness(abs_path, stored_commit)
        head_short = (current_head or "-")[:7]

        if is_stale and behind > 0:
            status = f"[yellow]{behind} new commit(s)[/yellow]"
            total_stale += 1
        elif is_stale:
            status = "[yellow]stale[/yellow]"
            total_stale += 1
        elif file_count > 0:
            status = "[green]up to date[/green]"
        else:
            status = "[yellow]empty[/yellow]"

        table.add_row(
            label, str(file_count), f"{symbol_count:,}", indexed_ago, head_short, status
        )

    console.print(table)

    # Summary line
    total_repos = len(ws_config.repos)
    indexed = sum(1 for e in ws_config.repos if (ws_root / e.path / ".repowise").exists())
    summary = f"\n  {indexed}/{total_repos} repos indexed. Default: {ws_config.default_repo}"
    if total_stale:
        summary += f". [yellow]{total_stale} stale[/yellow]"
    console.print(summary)


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("status")
@click.argument("path", required=False, default=None)
@click.option(
    "--workspace",
    "-w",
    is_flag=True,
    default=False,
    help="Show status for all repos in the workspace.",
)
def status_command(path: str | None, workspace: bool) -> None:
    """Show wiki sync state and page statistics."""
    repo_path = resolve_repo_path(path)

    if workspace:
        _workspace_status(repo_path)
        return

    repowise_dir = get_repowise_dir(repo_path)

    if not repowise_dir.exists():
        console.print("[yellow]No .repowise/ directory found. Run 'repowise init' first.[/yellow]")
        return

    state = load_state(repo_path)

    # State table
    state_table = Table(title="Sync State")
    state_table.add_column("Key", style="cyan")
    state_table.add_column("Value")
    state_table.add_row("Last sync commit", state.get("last_sync_commit", "—") or "—")
    state_table.add_row("Total pages", str(state.get("total_pages", 0)))
    state_table.add_row("Provider", state.get("provider", "—") or "—")
    state_table.add_row("Model", state.get("model", "—") or "—")
    state_table.add_row("Total tokens", f"{state.get('total_tokens', 0):,}")
    console.print(state_table)

    # Page counts from DB
    db_path = repowise_dir / "wiki.db"
    if not db_path.exists():
        console.print("[yellow]Database not found.[/yellow]")
        return

    async def _query_pages():
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_repository_by_path,
            get_session,
            list_pages,
        )

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        sf = create_session_factory(engine)

        counts: dict[str, int] = {}
        total_tokens = 0

        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                await engine.dispose()
                return counts, total_tokens
            pages = await list_pages(session, repo.id, limit=10000)
            for p in pages:
                counts[p.page_type] = counts.get(p.page_type, 0) + 1
                total_tokens += (p.input_tokens or 0) + (p.output_tokens or 0)

        await engine.dispose()
        return counts, total_tokens

    counts, total_db_tokens = run_async(_query_pages())

    if counts:
        pages_table = Table(title="Pages by Type")
        pages_table.add_column("Page Type", style="cyan")
        pages_table.add_column("Count", justify="right")
        for ptype, count in sorted(counts.items()):
            pages_table.add_row(ptype, str(count))
        pages_table.add_section()
        pages_table.add_row("[bold]Total[/bold]", f"[bold]{sum(counts.values())}[/bold]")
        pages_table.add_row("Total tokens", f"{total_db_tokens:,}")
        console.print(pages_table)
