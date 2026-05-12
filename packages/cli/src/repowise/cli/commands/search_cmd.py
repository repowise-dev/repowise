"""``repowise search`` — full-text, semantic, and symbol search."""

from __future__ import annotations

import click
from rich.table import Table

from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    get_db_url_for_repo,
    resolve_command_target,
    run_async,
)


@click.command("search")
@click.argument("query")
@click.argument("path", required=False, default=None)
@click.option(
    "--mode",
    type=click.Choice(["fulltext", "semantic", "symbol"]),
    default="fulltext",
    help="Search mode.",
)
@click.option("--limit", type=int, default=10, help="Max results.")
@click.option(
    "--repo",
    "repo_alias",
    default=None,
    help="Workspace repo alias to search (implies workspace mode).",
)
@click.option(
    "--all",
    "search_all",
    is_flag=True,
    default=False,
    help="In workspace mode, fan out across every indexed repo.",
)
@click.option(
    "--no-workspace",
    is_flag=True,
    default=False,
    help="Force single-repo mode even when invoked from a workspace.",
)
def search_command(
    query: str,
    path: str | None,
    mode: str,
    limit: int,
    repo_alias: str | None,
    search_all: bool,
    no_workspace: bool,
) -> None:
    """Search wiki pages by keyword, meaning, or symbol name.

    In workspace mode, defaults to the primary repo; pass --repo <alias>
    for a specific one or --all to search across every indexed repo.
    """
    from pathlib import Path

    target = resolve_command_target(
        path=path,
        no_workspace_flag=no_workspace,
        repo_alias=repo_alias,
    )
    target.notice(console, command=f"search ({mode})")

    repo_paths: list[Path] = []
    if target.is_workspace:
        assert target.ws_root is not None and target.ws_config is not None
        if search_all:
            repo_paths = [(target.ws_root / e.path).resolve() for e in target.ws_config.repos]
        elif target.repo_filter is not None:
            picked = target.resolve_repo_alias(target.repo_filter)
            if picked is None:
                raise click.ClickException(f"Unknown repo alias: {target.repo_filter}")
            repo_paths = [picked]
        else:
            primary = target.primary_path()
            if primary is None:
                raise click.ClickException("Workspace has no primary repo configured.")
            repo_paths = [primary]
    else:
        assert target.repo_path is not None
        repo_paths = [target.repo_path]

    repo_paths = [p for p in repo_paths if (p / ".repowise").is_dir()]
    if not repo_paths:
        console.print("[yellow]No indexed repos to search. Run 'repowise init' first.[/yellow]")
        return

    if len(repo_paths) == 1:
        repo_path = repo_paths[0]
        ensure_repowise_dir(repo_path)
        if mode == "fulltext":
            _search_fulltext(repo_path, query, limit)
        elif mode == "semantic":
            _search_semantic(repo_path, query, limit)
        elif mode == "symbol":
            _search_symbol(repo_path, query, limit)
        return

    # Multi-repo fan-out — gather, sort by score, render once.
    all_results: list = []
    for rp in repo_paths:
        try:
            if mode == "fulltext":
                results = _collect_fulltext(rp, query, limit)
            elif mode == "semantic":
                results = _collect_semantic(rp, query, limit)
            else:  # symbol
                results = _collect_symbol(rp, query, limit)
        except Exception as exc:
            console.print(f"[yellow]search failed for {rp.name}: {exc}[/yellow]")
            continue
        for r in results:
            all_results.append((rp.name, r))

    if not all_results:
        console.print("[yellow]No results across the workspace.[/yellow]")
        return

    if mode == "symbol":
        _render_symbol_rows(all_results, query, limit, multi=True)
    else:
        # Sort by score desc, slice to limit
        all_results.sort(key=lambda pair: getattr(pair[1], "score", 0.0), reverse=True)
        all_results = all_results[:limit]
        _display_results_multi(all_results, f"{mode.capitalize()} search: '{query}' (workspace)")


def _search_fulltext(repo_path, query: str, limit: int) -> None:
    async def _run():
        from repowise.core.persistence import FullTextSearch, create_engine

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        fts = FullTextSearch(engine)
        results = await fts.search(query, limit=limit)
        await engine.dispose()
        return results

    results = run_async(_run())
    _display_results(results, f"Full-text search: '{query}'")


def _search_semantic(repo_path, query: str, limit: int) -> None:
    async def _run():
        from pathlib import Path

        from repowise.core.persistence import MockEmbedder

        # Try LanceDB first (populated during repowise init)
        lance_dir = Path(repo_path) / ".repowise" / "lancedb"
        if lance_dir.exists():
            try:
                from repowise.cli.commands.init_cmd import _resolve_embedder
                from repowise.core.persistence.vector_store import LanceDBVectorStore

                embedder_name = _resolve_embedder(None)
                if embedder_name == "gemini":
                    from repowise.core.providers.embedding.gemini import GeminiEmbedder

                    embedder = GeminiEmbedder()
                elif embedder_name == "openai":
                    from repowise.core.providers.embedding.openai import OpenAIEmbedder

                    embedder = OpenAIEmbedder()
                else:
                    embedder = MockEmbedder()
                store = LanceDBVectorStore(str(lance_dir), embedder=embedder)
                results = await store.search(query, limit=limit)
                await store.close()
                return results
            except Exception:
                pass

        # Fallback to FTS
        from repowise.core.persistence import FullTextSearch, create_engine

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        fts = FullTextSearch(engine)
        results = await fts.search(query, limit=limit)
        await engine.dispose()
        return results

    results = run_async(_run())
    _display_results(results, f"Semantic search: '{query}'")


def _search_symbol(repo_path, query: str, limit: int) -> None:
    async def _run():
        from sqlalchemy import text as sa_text

        from repowise.core.persistence import create_engine, create_session_factory, get_session

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        sf = create_session_factory(engine)

        async with get_session(sf) as session:
            result = await session.execute(
                sa_text(
                    "SELECT name, qualified_name, kind, file_path, start_line "
                    "FROM wiki_symbols WHERE name LIKE :pattern LIMIT :limit"
                ),
                {"pattern": f"%{query}%", "limit": limit},
            )
            rows = result.fetchall()

        await engine.dispose()
        return rows

    rows = run_async(_run())

    table = Table(title=f"Symbol search: '{query}'")
    table.add_column("Name", style="cyan")
    table.add_column("Qualified Name")
    table.add_column("Kind")
    table.add_column("File")
    table.add_column("Line", justify="right")

    for row in rows:
        table.add_row(str(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4]))

    if not rows:
        console.print(f"[yellow]No symbols matching '{query}'[/yellow]")
    else:
        console.print(table)


def _display_results(results, title: str) -> None:
    table = Table(title=title)
    table.add_column("Score", justify="right", style="green")
    table.add_column("Title", style="cyan")
    table.add_column("Type")
    table.add_column("Path")
    table.add_column("Snippet", max_width=50)

    for r in results:
        table.add_row(
            f"{r.score:.3f}",
            r.title or "",
            r.page_type or "",
            r.target_path or "",
            (r.snippet or "")[:50],
        )

    if not results:
        console.print("[yellow]No results found.[/yellow]")
    else:
        console.print(table)


# ---------------------------------------------------------------------------
# Multi-repo (workspace fan-out) helpers
# ---------------------------------------------------------------------------


def _collect_fulltext(repo_path, query: str, limit: int):
    async def _run():
        from repowise.core.persistence import FullTextSearch, create_engine

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        fts = FullTextSearch(engine)
        results = await fts.search(query, limit=limit)
        await engine.dispose()
        return results

    return run_async(_run())


def _collect_semantic(repo_path, query: str, limit: int):
    async def _run():
        from pathlib import Path

        from repowise.core.persistence import FullTextSearch, MockEmbedder, create_engine

        lance_dir = Path(repo_path) / ".repowise" / "lancedb"
        if lance_dir.exists():
            try:
                from repowise.cli.commands.init_cmd import _resolve_embedder
                from repowise.core.persistence.vector_store import LanceDBVectorStore

                embedder_name = _resolve_embedder(None)
                if embedder_name == "gemini":
                    from repowise.core.providers.embedding.gemini import GeminiEmbedder

                    embedder = GeminiEmbedder()
                elif embedder_name == "openai":
                    from repowise.core.providers.embedding.openai import OpenAIEmbedder

                    embedder = OpenAIEmbedder()
                else:
                    embedder = MockEmbedder()
                store = LanceDBVectorStore(str(lance_dir), embedder=embedder)
                results = await store.search(query, limit=limit)
                await store.close()
                return results
            except Exception:
                pass

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        fts = FullTextSearch(engine)
        results = await fts.search(query, limit=limit)
        await engine.dispose()
        return results

    return run_async(_run())


def _collect_symbol(repo_path, query: str, limit: int):
    async def _run():
        from sqlalchemy import text as sa_text

        from repowise.core.persistence import create_engine, create_session_factory, get_session

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            result = await session.execute(
                sa_text(
                    "SELECT name, qualified_name, kind, file_path, start_line "
                    "FROM wiki_symbols WHERE name LIKE :pattern LIMIT :limit"
                ),
                {"pattern": f"%{query}%", "limit": limit},
            )
            rows = result.fetchall()
        await engine.dispose()
        return rows

    return run_async(_run())


def _display_results_multi(pairs, title: str) -> None:
    """Render fulltext/semantic results when fanned out across multiple repos."""
    table = Table(title=title)
    table.add_column("Score", justify="right", style="green")
    table.add_column("Repo", style="magenta")
    table.add_column("Title", style="cyan")
    table.add_column("Type")
    table.add_column("Path")
    table.add_column("Snippet", max_width=50)

    for repo_name, r in pairs:
        table.add_row(
            f"{r.score:.3f}",
            repo_name,
            r.title or "",
            r.page_type or "",
            r.target_path or "",
            (r.snippet or "")[:50],
        )
    console.print(table)


def _render_symbol_rows(pairs, query: str, limit: int, *, multi: bool) -> None:
    """Render symbol rows; ``pairs`` is a list of ``(repo_name, row)`` tuples."""
    title = f"Symbol search: '{query}' (workspace)" if multi else f"Symbol search: '{query}'"
    table = Table(title=title)
    table.add_column("Name", style="cyan")
    if multi:
        table.add_column("Repo", style="magenta")
    table.add_column("Qualified Name")
    table.add_column("Kind")
    table.add_column("File")
    table.add_column("Line", justify="right")

    for repo_name, row in pairs[:limit]:
        cells = [str(row[0])]
        if multi:
            cells.append(repo_name)
        cells += [str(row[1]), str(row[2]), str(row[3]), str(row[4])]
        table.add_row(*cells)

    if not pairs:
        console.print(f"[yellow]No symbols matching '{query}'[/yellow]")
    else:
        console.print(table)
