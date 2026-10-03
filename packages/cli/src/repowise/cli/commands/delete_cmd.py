"""``repowise delete`` — remove a repository and all its generated data."""

from __future__ import annotations

from pathlib import Path

import click
from rich.table import Table

from repowise.cli.helpers import (
    console,
    db_configured,
    get_db_url_for_repo,
    get_repowise_dir,
    resolve_repo_path,
    run_async,
)


def _same_local_path(stored: str | None, repo_path: Path) -> bool:
    """True when *stored* (a ``Repository.local_path``) names *repo_path*.

    Rows are written from a resolved absolute path, but a caller may reach the
    same repository through a symlink or a relative argument, so both sides are
    resolved before comparing. A path that no longer exists still compares by
    string rather than raising.
    """
    if not stored:
        return False
    try:
        return Path(stored).resolve() == repo_path
    except OSError:
        return stored == str(repo_path)


@click.command("delete")
@click.argument("repo_id", required=False, default=None)
@click.option("--force", "-f", is_flag=True, default=False, help="Skip confirmation prompt.")
@click.option("--path", "-p", default=None, help="Path to the repository to delete.")
def delete_command(repo_id: str | None, force: bool, path: str | None) -> None:
    """Delete a repository and all its generated data.

    ``--path`` (or the current directory) names the repository to delete: the
    row whose ``local_path`` matches it is selected. The path is only a
    database-location hint when nothing matches and no path was given, in which
    case every known repository is listed for a numbered selection.
    """
    repo_path = resolve_repo_path(path)
    repowise_dir = get_repowise_dir(repo_path)

    # These two checks describe the repo-local SQLite default, and only it. A
    # shared store (REPOWISE_DB_URL pointing at PostgreSQL) holds this
    # repository's index somewhere the repo has no file for, so demanding
    # .repowise/wiki.db here refused to delete a repository the configured
    # database knows about. With no configured URL the messages are unchanged.
    if not db_configured():
        if not repowise_dir.exists():
            console.print(
                "[yellow]No .repowise/ directory found. Run 'repowise init' first.[/yellow]"
            )
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

        # Find the target repo info
        target_id = repo_id
        if target_id is None and path is not None:
            # --path names a repository, so it selects that repository. The
            # numbered list stays for the no-path case only; offering to delete
            # some other row after the user named a path is how the wrong
            # repository gets removed from a shared database.
            match = next((r for r in repos if _same_local_path(r[2], repo_path)), None)
            if match is None:
                console.print(
                    f"[yellow]No repository in the database matches {repo_path}.[/yellow]"
                )
                await engine.dispose()
                return
            target_id = match[0]

        # If no repo_id given, let the user pick
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

        if not force and not click.confirm("Are you sure?", default=False):
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
