"""``repowise search`` — full-text, semantic, and symbol search."""

from __future__ import annotations

import click
from rich.table import Table

from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    get_db_url_for_repo,
    resolve_repo_path,
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
def search_command(
    query: str,
    path: str | None,
    mode: str,
    limit: int,
) -> None:
    """Search wiki pages by keyword, meaning, or symbol name."""
    repo_path = resolve_repo_path(path)
    ensure_repowise_dir(repo_path)

    if mode == "fulltext":
        _search_fulltext(repo_path, query, limit)
    elif mode == "semantic":
        _search_semantic(repo_path, query, limit)
    elif mode == "symbol":
        _search_symbol(repo_path, query, limit)


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

        from repowise.core.persistence import InMemoryVectorStore, MockEmbedder

        # Try LanceDB first (populated during repowise init)
        lance_dir = Path(repo_path) / ".repowise" / "lancedb"
        if lance_dir.exists():
            try:
                from repowise.core.persistence.vector_store import LanceDBVectorStore

                from repowise.cli.commands.init_cmd import _resolve_embedder

                embedder_name = _resolve_embedder(None)
                if embedder_name == "gemini":
                    from repowise.core.persistence.gemini_embedder import GeminiEmbedder

                    embedder = GeminiEmbedder()
                elif embedder_name == "openai":
                    from repowise.core.persistence.openai_embedder import OpenAIEmbedder

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
        console.print(f"[yellow]No results found.[/yellow]")
    else:
        console.print(table)
