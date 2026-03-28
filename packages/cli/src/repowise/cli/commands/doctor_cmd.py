"""``repowise doctor`` — health check for the wiki setup."""

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


def _check(name: str, ok: bool, detail: str = "") -> tuple[str, str, str]:
    status = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
    return (name, status, detail)


@click.command("doctor")
@click.argument("path", required=False, default=None)
def doctor_command(path: str | None) -> None:
    """Run health checks on the wiki setup."""
    repo_path = resolve_repo_path(path)
    checks: list[tuple[str, str, str]] = []

    # 1. Git repository?
    try:
        import git as gitpython

        gitpython.Repo(repo_path, search_parent_directories=True)
        checks.append(_check("Git repository", True, str(repo_path)))
    except Exception:
        checks.append(_check("Git repository", False, "Not a git repo"))

    # 2. .repowise/ exists?
    repowise_dir = get_repowise_dir(repo_path)
    checks.append(_check(".repowise/ directory", repowise_dir.exists(), str(repowise_dir)))

    # 3. Database connectable?
    db_path = repowise_dir / "wiki.db"
    db_ok = False
    page_count = 0
    if db_path.exists():
        try:

            async def _check_db():
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
                count = 0
                async with get_session(sf) as session:
                    repo = await get_repository_by_path(session, str(repo_path))
                    if repo:
                        pages = await list_pages(session, repo.id, limit=10000)
                        count = len(pages)
                await engine.dispose()
                return count

            page_count = run_async(_check_db())
            db_ok = True
        except Exception as e:
            checks.append(_check("Database", False, str(e)))
    if db_ok:
        checks.append(_check("Database", True, f"{page_count} pages"))
    elif not db_path.exists():
        checks.append(_check("Database", False, "wiki.db not found"))

    # 4. state.json valid?
    state = load_state(repo_path)
    state_ok = bool(state)
    checks.append(
        _check(
            "state.json",
            state_ok,
            f"last_sync: {(state.get('last_sync_commit') or '—')[:8]}"
            if state_ok
            else "Not found or empty",
        )
    )

    # 5. Provider importable?
    provider_ok = False
    try:
        from repowise.core.providers import list_providers

        providers = list_providers()
        provider_ok = len(providers) > 0
        checks.append(_check("Providers", provider_ok, ", ".join(providers)))
    except Exception as e:
        checks.append(_check("Providers", False, str(e)))

    # 6. Stale page count
    if db_ok and page_count > 0:
        try:

            async def _check_stale():
                from repowise.core.persistence import (
                    create_engine,
                    create_session_factory,
                    get_repository_by_path,
                    get_session,
                    get_stale_pages,
                )

                url = get_db_url_for_repo(repo_path)
                engine = create_engine(url)
                sf = create_session_factory(engine)
                async with get_session(sf) as session:
                    repo = await get_repository_by_path(session, str(repo_path))
                    if repo:
                        stale = await get_stale_pages(session, repo.id)
                        await engine.dispose()
                        return len(stale)
                await engine.dispose()
                return 0

            stale_count = run_async(_check_stale())
            checks.append(_check("Stale pages", stale_count == 0, f"{stale_count} stale"))
        except Exception:
            checks.append(_check("Stale pages", True, "Could not check"))

    # Display
    table = Table(title="repowise Doctor")
    table.add_column("Check", style="cyan")
    table.add_column("Status")
    table.add_column("Detail")
    for name, status, detail in checks:
        table.add_row(name, status, detail)
    console.print(table)

    all_ok = all("[green]OK[/green]" in status for _, status, _ in checks)
    if all_ok:
        console.print("[bold green]All checks passed![/bold green]")
    else:
        console.print("[bold yellow]Some checks failed.[/bold yellow]")
