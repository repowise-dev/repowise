"""``repowise delete`` — remove a repository and all its generated data."""

from __future__ import annotations

import click
from rich.table import Table

from repowise.cli.helpers import (
    console,
    get_db_url_for_repo,
    get_repowise_dir,
    resolve_repo_path,
    run_async,
)


@click.command("delete")
@click.argument("repo_id", required=False, default=None)
@click.option("--force", "-f", is_flag=True, default=False, help="Skip confirmation prompt.")
@click.option("--path", "-p", default=None, help="Path to the repository directory.")
def delete_command(repo_id: str | None, force: bool, path: str | None) -> None:
    """Delete a repository and all its generated data."""
    repo_path = resolve_repo_path(path)
    repowise_dir = get_repowise_dir(repo_path)

    if not repowise_dir.exists():
        console.print("[yellow]No .repowise/ directory found. Run 'repowise init' first.[/yellow]")
        return

    db_path = repowise_dir / "wiki.db"
    if not db_path.exists():
        console.print("[yellow]Database not found.[/yellow]")
        return

    async def _run() -> None:
        from sqlalchemy import func, select

        from repowise.core.persistence import (
            Repository,
            create_engine,
            create_session_factory,
            delete_repository,
            get_session,
            list_page_ids,
        )
        from repowise.core.persistence.models import Page
        from repowise.core.persistence.search import FullTextSearch

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        sf = create_session_factory(engine)

        # List all repos with page counts
        async with get_session(sf) as session:
            result = await session.execute(
                select(
                    Repository.id,
                    Repository.name,
                    Repository.local_path,
                    func.count(Page.id).label("page_count"),
                )
                .outerjoin(Page, Page.repository_id == Repository.id)
                .group_by(Repository.id)
                .order_by(Repository.updated_at.desc())
            )
            repos = list(result.all())

        if not repos:
            console.print("[yellow]No repositories found in the database.[/yellow]")
            await engine.dispose()
            return

        # If no repo_id given, let the user pick
        target_id = repo_id
        if target_id is None:
            table = Table(title="Repositories")
            table.add_column("#", style="cyan", justify="right")
            table.add_column("Name", style="bold")
            table.add_column("Path")
            table.add_column("Pages", justify="right")
            table.add_column("ID", style="dim")

            for i, (rid, name, lpath, pcount) in enumerate(repos, 1):
                table.add_row(str(i), name, lpath, str(pcount), rid[:12])

            console.print(table)
            choice = click.prompt(
                "Enter number to delete (or 'q' to quit)",
                default="q",
            )
            if choice.lower() == "q":
                await engine.dispose()
                return
            try:
                idx = int(choice) - 1
                if idx < 0 or idx >= len(repos):
                    raise ValueError
                target_id = repos[idx][0]
            except (ValueError, IndexError):
                console.print("[red]Invalid selection.[/red]")
                await engine.dispose()
                return

        # Find the target repo info
        target = next((r for r in repos if r[0] == target_id), None)
        if target is None:
            console.print(f"[red]Repository {target_id} not found.[/red]")
            await engine.dispose()
            return

        rid, name, lpath, pcount = target
        console.print(
            f"\nAbout to delete [bold]{name}[/bold] ({lpath}) — "
            f"[yellow]{pcount} pages[/yellow] will be removed."
        )

        if not force:
            if not click.confirm("Are you sure?", default=False):
                console.print("Cancelled.")
                await engine.dispose()
                return

        # Collect page IDs, clean FTS, delete repo
        async with get_session(sf) as session:
            page_ids = await list_page_ids(session, rid)

            fts = FullTextSearch(engine)
            await fts.delete_many(page_ids)

            await delete_repository(session, rid)

        console.print(
            f"[bold green]Deleted[/bold green] {name} — "
            f"{len(page_ids)} pages removed."
        )

        await engine.dispose()

    run_async(_run())
