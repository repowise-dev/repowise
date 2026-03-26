"""``repowise status`` — show sync state and page counts."""

from __future__ import annotations

import click
from rich.table import Table

from repowise.cli.helpers import (
    console,
    get_db_url_for_repo,
    get_repowise_dir,
    load_state,
    resolve_repo_path,
    run_async,
)


@click.command("status")
@click.argument("path", required=False, default=None)
def status_command(path: str | None) -> None:
    """Show wiki sync state and page statistics."""
    repo_path = resolve_repo_path(path)
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
