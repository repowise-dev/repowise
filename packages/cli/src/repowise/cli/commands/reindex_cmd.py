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
from repowise.cli.ui import BRAND_STYLE, OWL_SPINNER


@click.command("reindex")
@click.argument("path", required=False, default=None)
@click.option(
    "--embedder",
    type=click.Choice(["gemini", "openai", "openrouter", "ollama", "edenai", "mock", "auto"]),
    default="auto",
    help="Embedder to use. 'auto' detects from env vars / config.",
)
@click.option("--batch-size", type=int, default=32, help="Pages per embedding batch.")
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
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from repowise.cli.providers.embedders import build_embedder, resolve_embedder_for_repo
    from repowise.core.persistence.database import create_engine, init_db
    from repowise.core.persistence.models import Page
    from repowise.core.providers.embedding.base import MockEmbedder

    # --- Resolve embedder ---
    if embedder_name == "auto":
        # Resolve through the repo, not the environment. `_resolve_embedder(None)`
        # reads REPOWISE_EMBEDDER and API keys only, so `auto` never saw the
        # `embedder` pin in config.yaml despite this flag's help text promising
        # "env vars / config". A repo pinned to a keyless embedder therefore
        # aborted with "No real embedder available", and a repo pinned to one
        # embedder in a shell exporting a different provider's key had its table
        # REWRITTEN by that other provider — the exact writer/reader split
        # `resolve_embedder_for_repo` was added to prevent, and one this command
        # is on the writing side of.
        embedder_name = resolve_embedder_for_repo(repo_path)

    embedder_impl = build_embedder(embedder_name, repo_path)
    # Guard on the RESOLVED name, not the flag. `mock` reached here two ways:
    # asked for, or fallen back to because nothing else was configured. Only the
    # second is an error. Now that `auto` reads the pin, `embedder: mock` in
    # config.yaml is a third way, and it is as deliberate as passing the flag —
    # comparing against `requested_embedder` would abort a repo that is pinned
    # to mock on purpose.
    if isinstance(embedder_impl, MockEmbedder) and embedder_name != "mock":
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
    # The recipe is shared with generation and ``doctor --repair`` so a page
    # reindexed here gets the same vector it would have got from any of them.
    from repowise.core.persistence.vector_store import embed_item

    indexed = 0
    failed = 0
    below_floor = 0

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
                if not (page.title or "").strip():
                    # The shared recipe refuses a blank title, and it is right
                    # to: the row would be unfindable by name. Here that must
                    # not abort a whole reindex over one bad row, so the page
                    # is skipped and counted like any other failure.
                    failed += 1
                    warned += 1
                    if warned <= 3:
                        console.print(
                            f"[yellow]  Warning: skipped {page.id}: no title to index it by"
                            "[/yellow]"
                        )
                    continue
                item = embed_item(
                    page.id,
                    title=page.title,
                    page_type=page.page_type or "",
                    target_path=page.target_path or "",
                    summary=page.summary or "",
                    content=page.content or "",
                )
                if item is None:
                    # Below the information floor. Not a failure — the page is
                    # deliberately kept out of the index and is counted apart
                    # from the ones that broke, because a reindex reporting
                    # them together would read as an embedder losing rows.
                    below_floor += 1
                    continue
                items.append(item)
            await _embed_slice(items)
            progress.advance(task, advance=len(batch))

        # Decision records — embedded into the shared page store under the
        # decision: namespace, batched like the pages. Uses embed_batch
        # directly (which raises on failure) rather than the ingest-side
        # best-effort wrapper, so the indexed/failed counters stay honest.
        # Only when there are any. Set unconditionally, this relabelled a bar
        # that had just finished the pages, so a repo with no decisions ended
        # its reindex reading "Indexing decisions... 186/186" — 186 pages
        # reported as decisions that were never indexed.
        if decisions:
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

    # Record the embedder we actually built the table with. Without this, a
    # repo indexed keyless keeps `embedder: mock` in config.yaml, and the next
    # `repowise update` builds a mock store, writes 8-wide vectors into the
    # 1536-wide table this run just built, and LanceDB resolves the mismatch by
    # dropping the table — every page and decision vector gone, silently. The
    # reindex undone by a routine update.
    #
    # Only when something was actually written: the pin describes the table, so
    # a run where every item failed has nothing to describe, and claiming
    # otherwise would point later writers at a width the table does not have.
    if indexed:
        from repowise.cli.helpers import save_config_partial

        save_config_partial(Path(repo_path), embedder=embedder_name)

    console.print(
        f"\n[bold green]Done![/bold green] Indexed {indexed} items"
        + (f" ({failed} failed)" if failed else "")
        # Named separately from failures, and only when it happened: a count
        # folded into "failed" would read as an embedder losing rows, and a
        # standing "0 held back" would read as a problem on every run that
        # never had one.
        + (f" ({below_floor} held back as too thin to index)" if below_floor else "")
        + f" -> {lance_dir}"
    )

    # A reindex that indexed nothing but failed on every item is a failed
    # build, not a success: an automated pipeline (or an agent) must not treat
    # an empty vector index as a successful reindex. Exit non-zero so the
    # failure is visible in the exit status, not just in the printed count.
    if indexed == 0 and failed > 0:
        raise click.Abort()
