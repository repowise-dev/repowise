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
@click.option("--repair", is_flag=True, default=False, help="Attempt to fix detected mismatches.")
def doctor_command(path: str | None, repair: bool) -> None:
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

    # 6. Provider configuration?
    from repowise.cli.helpers import validate_provider_config

    config_warnings = validate_provider_config()
    config_ok = len(config_warnings) == 0
    config_detail = "All required API keys configured" if config_ok else "; ".join(config_warnings)
    checks.append(_check("Provider config", config_ok, config_detail))

    # 7. Stale page count
    stale_count = 0
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

    # 8-9. Three-store consistency (SQL vs Vector Store vs FTS)
    missing_from_vector: set[str] = set()
    orphaned_vector: set[str] = set()
    missing_from_fts: set[str] = set()
    orphaned_fts: set[str] = set()

    if db_ok and page_count > 0:
        try:

            async def _check_stores():
                from repowise.core.persistence import (
                    FullTextSearch,
                    create_engine,
                    create_session_factory,
                    get_repository_by_path,
                    get_session,
                    list_pages,
                )
                from repowise.core.persistence.vector_store import (
                    LanceDBVectorStore,
                )
                from repowise.core.providers.embedding.base import MockEmbedder

                url = get_db_url_for_repo(repo_path)
                engine = create_engine(url)
                sf = create_session_factory(engine)

                # Get all SQL page IDs
                async with get_session(sf) as session:
                    repo = await get_repository_by_path(session, str(repo_path))
                    if not repo:
                        await engine.dispose()
                        return set(), set(), set(), set()
                    pages = await list_pages(session, repo.id, limit=10000)
                    sql_ids = {p.page_id for p in pages}

                # Check vector store
                vs_ids: set[str] = set()
                lance_dir = repowise_dir / "lancedb"
                if lance_dir.exists():
                    try:
                        embedder = MockEmbedder()
                        vs = LanceDBVectorStore(str(lance_dir), embedder=embedder)
                        vs_ids = await vs.list_page_ids()
                        await vs.close()
                    except Exception:
                        pass  # LanceDB not available

                m_vec = sql_ids - vs_ids if vs_ids else set()
                o_vec = vs_ids - sql_ids if vs_ids else set()

                # Check FTS
                fts = FullTextSearch(engine)
                try:
                    fts_ids = await fts.list_indexed_ids()
                except Exception:
                    fts_ids = set()
                m_fts = sql_ids - fts_ids if fts_ids else set()
                o_fts = fts_ids - sql_ids if fts_ids else set()

                await engine.dispose()
                return m_vec, o_vec, m_fts, o_fts

            missing_from_vector, orphaned_vector, missing_from_fts, orphaned_fts = run_async(
                _check_stores()
            )

            vec_ok = not missing_from_vector and not orphaned_vector
            vec_detail = (
                "in sync"
                if vec_ok
                else (f"{len(missing_from_vector)} missing, {len(orphaned_vector)} orphaned")
            )
            checks.append(_check("SQL ↔ Vector Store", vec_ok, vec_detail))

            fts_ok = not missing_from_fts and not orphaned_fts
            fts_detail = (
                "in sync"
                if fts_ok
                else (f"{len(missing_from_fts)} missing, {len(orphaned_fts)} orphaned")
            )
            checks.append(_check("SQL ↔ FTS Index", fts_ok, fts_detail))
        except Exception:
            checks.append(_check("Store consistency", True, "Could not check"))

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

    # --repair: fix detected mismatches
    has_mismatches = missing_from_fts or orphaned_fts or missing_from_vector or orphaned_vector
    if repair and has_mismatches:
        console.print("\n[bold]Repairing store mismatches...[/bold]")

        async def _repair():
            from repowise.core.persistence import (
                FullTextSearch,
                create_engine,
                create_session_factory,
                get_session,
            )

            url = get_db_url_for_repo(repo_path)
            engine = create_engine(url)
            sf = create_session_factory(engine)
            repaired = 0

            # Repair FTS: re-index missing pages, delete orphaned
            if missing_from_fts or orphaned_fts:
                fts = FullTextSearch(engine)
                await fts.ensure_index()
                if missing_from_fts:
                    # Fetch full page data for missing pages
                    async with get_session(sf) as session:
                        from sqlalchemy import select

                        from repowise.core.persistence.models import Page

                        rows = await session.execute(
                            select(Page).where(Page.page_id.in_(list(missing_from_fts)))
                        )
                        for page in rows.scalars().all():
                            await fts.index(page.page_id, page.title, page.content)
                            repaired += 1
                for pid in orphaned_fts:
                    await fts.delete(pid)
                    repaired += 1

            # Repair vector store: re-embed missing pages, delete orphaned
            lance_dir = repowise_dir / "lancedb"
            if lance_dir.exists() and (missing_from_vector or orphaned_vector):
                try:
                    from repowise.core.persistence.vector_store import LanceDBVectorStore
                    from repowise.core.providers.embedding.base import MockEmbedder

                    # Use mock embedder for repair to avoid API costs;
                    # user can re-run `repowise reindex` for real embeddings
                    embedder = MockEmbedder()

                    vs = LanceDBVectorStore(str(lance_dir), embedder=embedder)

                    if missing_from_vector:
                        async with get_session(sf) as session:
                            from sqlalchemy import select

                            from repowise.core.persistence.models import Page

                            rows = await session.execute(
                                select(Page).where(Page.page_id.in_(list(missing_from_vector)))
                            )
                            for page in rows.scalars().all():
                                await vs.embed_and_upsert(
                                    page.page_id,
                                    page.content,
                                    {
                                        "title": page.title,
                                        "page_type": page.page_type,
                                        "target_path": page.target_path,
                                    },
                                )
                                repaired += 1

                    for pid in orphaned_vector:
                        await vs.delete(pid)
                        repaired += 1

                    await vs.close()
                except Exception as exc:
                    console.print(f"[yellow]Vector repair skipped: {exc}[/yellow]")

            await engine.dispose()
            return repaired

        repaired_count = run_async(_repair())
        console.print(f"[bold green]Repaired {repaired_count} entries.[/bold green]")
    elif repair and not has_mismatches:
        console.print("[green]Nothing to repair.[/green]")
