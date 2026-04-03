"""``repowise reindex`` — rebuild vector embeddings from existing wiki pages."""

from __future__ import annotations

import click
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    get_db_url_for_repo,
    resolve_repo_path,
    run_async,
)


@click.command("reindex")
@click.argument("path", required=False, default=None)
@click.option(
    "--embedder",
    type=click.Choice(["gemini", "openai", "ollama", "auto"]),
    default="auto",
    help="Embedder to use. 'auto' detects from env vars / config.",
)
@click.option("--batch-size", type=int, default=20, help="Pages per embedding batch.")
def reindex_command(path: str | None, embedder: str, batch_size: int) -> None:
    """Rebuild vector search index from existing wiki pages.

    Reads all pages from the database, embeds them using the configured
    embedder, and persists the vectors to LanceDB. No LLM calls — only
    embedding API calls. Fast and cheap.
    """
    repo_path = resolve_repo_path(path)
    ensure_repowise_dir(repo_path)

    # Load saved API keys from .repowise/.env (won't overwrite existing env vars)
    from repowise.cli.ui import load_dotenv

    load_dotenv(repo_path)

    run_async(_reindex(repo_path, embedder, batch_size))


async def _reindex(repo_path, embedder_name: str, batch_size: int) -> None:
    from pathlib import Path

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from repowise.core.persistence.database import init_db
    from repowise.core.persistence.models import Page

    # --- Resolve embedder ---
    if embedder_name == "auto":
        from repowise.cli.commands.init_cmd import _resolve_embedder

        embedder_name = _resolve_embedder(None)

    if embedder_name == "gemini":
        from repowise.core.providers.embedding.gemini import GeminiEmbedder

        embedder_impl = GeminiEmbedder()
        console.print("[green]Using Gemini embedder[/green]")
    elif embedder_name == "openai":
        from repowise.core.providers.embedding.openai import OpenAIEmbedder

        embedder_impl = OpenAIEmbedder()
        console.print("[green]Using OpenAI embedder[/green]")
    elif embedder_name == "ollama":
        from repowise.core.providers.embedding.ollama import OllamaEmbedder

        embedder_impl = OllamaEmbedder()
        console.print("[green]Using Ollama embedder (local)[/green]")
    else:
        console.print(
            "[red]No real embedder available. Set GEMINI_API_KEY, OPENAI_API_KEY, "
            "or use --embedder ollama (requires local Ollama server).[/red]"
        )
        raise click.Abort()

    # --- Create LanceDB vector store ---
    lance_dir = Path(repo_path) / ".repowise" / "lancedb"
    try:
        from repowise.core.persistence.vector_store import LanceDBVectorStore
    except ImportError:
        console.print("[red]lancedb not installed. Run: uv pip install lancedb[/red]")
        raise click.Abort() from None

    lance_dir.mkdir(parents=True, exist_ok=True)
    vector_store = LanceDBVectorStore(str(lance_dir), embedder=embedder_impl)

    # Also create a decision store for decision records
    decision_store = LanceDBVectorStore(
        str(lance_dir), embedder=embedder_impl, table_name="decision_records"
    )

    # --- Open database ---
    db_url = get_db_url_for_repo(repo_path)
    connect_args = {}
    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_async_engine(db_url, connect_args=connect_args)
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # --- Load all pages ---
    async with factory() as session:
        result = await session.execute(select(Page))
        pages = list(result.scalars().all())

    # --- Load decision records ---
    from repowise.core.persistence.models import DecisionRecord

    async with factory() as session:
        result = await session.execute(select(DecisionRecord))
        decisions = list(result.scalars().all())

    total = len(pages) + len(decisions)
    console.print(
        f"Found [bold]{len(pages)}[/bold] wiki pages and [bold]{len(decisions)}[/bold] decision records to index."
    )

    if total == 0:
        console.print("[yellow]Nothing to index. Run 'repowise init' first.[/yellow]")
        await engine.dispose()
        return

    # --- Embed and upsert pages in batches ---
    indexed = 0
    failed = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Indexing pages...", total=total)

        # Pages
        for i in range(0, len(pages), batch_size):
            batch = pages[i : i + batch_size]
            for page in batch:
                try:
                    text = f"{page.title}\n{page.content}" if page.content else page.title or ""
                    await vector_store.embed_and_upsert(
                        page.id,
                        text,
                        {
                            "title": page.title or "",
                            "page_type": page.page_type or "",
                            "target_path": page.target_path or "",
                        },
                    )
                    indexed += 1
                except Exception as exc:
                    failed += 1
                    if failed <= 3:
                        console.print(
                            f"[yellow]  Warning: failed to embed {page.id}: {exc}[/yellow]"
                        )
                progress.advance(task)

        # Decision records
        progress.update(task, description="Indexing decisions...")
        for i in range(0, len(decisions), batch_size):
            batch = decisions[i : i + batch_size]
            for d in batch:
                try:
                    text = f"{d.title}\n{d.decision}\n{d.rationale}"
                    await decision_store.embed_and_upsert(
                        d.id,
                        text,
                        {
                            "title": d.title or "",
                            "page_type": "decision_record",
                            "target_path": "",
                        },
                    )
                    indexed += 1
                except Exception as exc:
                    failed += 1
                    if failed <= 3:
                        console.print(
                            f"[yellow]  Warning: failed to embed decision {d.id}: {exc}[/yellow]"
                        )
                progress.advance(task)

    await vector_store.close()
    await decision_store.close()
    await engine.dispose()

    console.print(
        f"\n[bold green]Done![/bold green] Indexed {indexed} items"
        + (f" ({failed} failed)" if failed else "")
        + f" -> {lance_dir}"
    )
