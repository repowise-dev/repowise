"""``repowise reindex`` — rebuild vector embeddings from existing wiki pages."""

from __future__ import annotations

import click
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from repowise.cli._setup import configure_cli_logging
from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    get_db_url_for_repo,
    resolve_repo_path,
    run_async,
)
from repowise.cli.ui import BRAND_STYLE, OWL_SPINNER


@click.command("reindex")
@click.argument("path", required=False, default=None)
@click.option(
    "--embedder",
    type=click.Choice(["gemini", "openai", "openrouter", "ollama", "mock", "auto"]),
    default="auto",
    help="Embedder to use. 'auto' detects from env vars / config.",
)
@click.option("--batch-size", type=int, default=32, help="Pages per embedding batch.")
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Show debug logs from the pipeline.",
)
def reindex_command(
    path: str | None, embedder: str, batch_size: int, verbose: bool = False
) -> None:
    """Rebuild vector search index from existing wiki pages.

    Reads all pages from the database, embeds them using the configured
    embedder, and persists the vectors to LanceDB. No LLM calls — only
    embedding API calls. Fast and cheap.
    """
    # Quiet library/structlog output so it doesn't interleave with the live
    # progress bar; `-v` lets repowise's debug lines through for troubleshooting.
    configure_cli_logging(verbose=verbose)

    repo_path = resolve_repo_path(path)
    ensure_repowise_dir(repo_path)

    # Load saved API keys from .repowise/.env (won't overwrite existing env vars)
    from repowise.cli.ui import load_dotenv

    load_dotenv(repo_path)

    run_async(_reindex(repo_path, embedder, batch_size))


async def _reindex(repo_path, embedder_name: str, batch_size: int) -> None:
    from pathlib import Path

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from repowise.cli.providers.embedders import build_embedder
    from repowise.core.persistence.database import create_engine, init_db
    from repowise.core.persistence.models import Page
    from repowise.core.providers.embedding.base import MockEmbedder

    # --- Resolve embedder ---
    requested_embedder = embedder_name
    if embedder_name == "auto":
        from repowise.cli.commands.init_cmd import _resolve_embedder

        embedder_name = _resolve_embedder(None)

    embedder_impl = build_embedder(embedder_name)
    if isinstance(embedder_impl, MockEmbedder) and requested_embedder != "mock":
        console.print(
            "[red]No real embedder available. Set a real embedder key, configure Ollama, or pass --embedder mock for test vectors.[/red]"
        )
        raise click.Abort()
    if embedder_name == "mock":
        console.print("[yellow]Using mock embedder (deterministic test vectors)[/yellow]")
    else:
        console.print(f"[green]Using {embedder_name} embedder[/green]")

    # --- Create LanceDB vector store ---
    lance_dir = Path(repo_path) / ".repowise" / "lancedb"
    try:
        from repowise.core.persistence.vector_store import LanceDBVectorStore
    except ImportError:
        console.print("[red]lancedb not installed. Run: uv pip install lancedb[/red]")
        raise click.Abort() from None

    lance_dir.mkdir(parents=True, exist_ok=True)
    vector_store = LanceDBVectorStore(str(lance_dir), embedder=embedder_impl)

    # --- Open database ---
    db_url = get_db_url_for_repo(repo_path)
    engine = create_engine(db_url)
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # --- Load all pages ---
    async with factory() as session:
        result = await session.execute(select(Page))
        pages = list(result.scalars().all())

    # --- Load decision records ---
    from repowise.core.analysis.decision_semantic_match import decision_vector_item
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
        SpinnerColumn(spinner_name=OWL_SPINNER, style=BRAND_STYLE),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Indexing pages...", total=total)

        warned = 0

        async def _embed_slice(items: list[tuple[str, str, dict]]) -> None:
            """Embed one slice batched; on failure retry per item.

            The batched call is the fast path (one embedder request per
            chunk). A raised error falls back to per-item embedding so one
            poison item can't sink its neighbours, and the indexed/failed
            counters stay per-item accurate.
            """
            nonlocal indexed, failed, warned
            try:
                await vector_store.embed_batch(items)
                indexed += len(items)
                return
            except Exception:
                pass
            for page_id, text, meta in items:
                try:
                    await vector_store.embed_and_upsert(page_id, text, meta)
                    indexed += 1
                except Exception as exc:
                    failed += 1
                    warned += 1
                    if warned <= 3:
                        console.print(
                            f"[yellow]  Warning: failed to embed {page_id}: {exc}[/yellow]"
                        )

        # Pages — one batched embed per slice instead of one embedder
        # round-trip per page (a large wiki paid thousands of serial calls).
        for i in range(0, len(pages), batch_size):
            batch = pages[i : i + batch_size]
            items = []
            for page in batch:
                text = f"{page.title}\n{page.content}" if page.content else page.title or ""
                if not text.strip():
                    continue  # embedders reject empty input; nothing to index
                items.append(
                    (
                        page.id,
                        text,
                        {
                            "title": page.title or "",
                            "page_type": page.page_type or "",
                            "target_path": page.target_path or "",
                        },
                    )
                )
            await _embed_slice(items)
            progress.advance(task, advance=len(batch))

        # Decision records — embedded into the shared page store under the
        # decision: namespace, batched like the pages. Uses embed_batch
        # directly (which raises on failure) rather than the ingest-side
        # best-effort wrapper, so the indexed/failed counters stay honest.
        progress.update(task, description="Indexing decisions...")
        for i in range(0, len(decisions), batch_size):
            batch = decisions[i : i + batch_size]
            items = [
                item
                for d in batch
                if (
                    item := decision_vector_item(
                        d.id,
                        title=d.title or "",
                        decision=d.decision or "",
                        evidence_file=getattr(d, "evidence_file", None),
                    )
                )
                is not None
            ]
            await _embed_slice(items)
            progress.advance(task, advance=len(batch))

    await vector_store.close()
    await engine.dispose()

    console.print(
        f"\n[bold green]Done![/bold green] Indexed {indexed} items"
        + (f" ({failed} failed)" if failed else "")
        + f" -> {lance_dir}"
    )
