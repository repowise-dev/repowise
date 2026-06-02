"""Incremental, resume-friendly page generation for ``repowise init``.

The orchestrator's :func:`run_generation` buffers every page in memory and the
CLI writes them to the database only once, at the very end of the run, from
``_persist_result``. That is fine for a clean run but loses everything when a
long generation phase is interrupted: the pages already embedded into the
vector store are skipped on the next resume (the store is the resume ground
truth) yet were never written to the ``pages`` table, so the wiki ends up
permanently missing them.

:func:`run_generation_with_persistence` closes that gap with two cooperating
mechanisms, both best-effort so neither can fail a generation run:

* **prior-page reuse** — every persisted page is loaded up front and handed to
  the generator, which skips the LLM call whenever a freshly rendered prompt
  still hashes to the stored value under the same model (the same reuse
  ``repowise update`` performs).
* **incremental flush** — each page is written to the database the instant it
  is generated, via the generator's ``on_page_ready`` sink. An interrupt then
  leaves a usable, partially-complete wiki on disk, and the next resume reuses
  those pages instead of regenerating them.

The flush runs on its own engine and is the sole writer of ``wiki.db`` during
generation (cost rows are buffered and flushed afterwards, issue #326), so it
introduces no write contention. Writes are idempotent — :func:`upsert_page`
is a no-op when content, prompt hash and model are unchanged — so the
end-of-run ``_persist_result`` re-write of the same pages neither bumps their
version nor spawns redundant history.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Hard cap on how long the drain of the persistence queue may take after
# generation finishes, so a wedged DB write can never hang the CLI.
_DRAIN_TIMEOUT_SECS = 60.0


async def run_generation_with_persistence(
    *,
    repo_path: Path,
    repo_name: str,
    reuse_prior_pages: bool = True,
    **generation_kwargs: Any,
) -> list[Any]:
    """Run :func:`run_generation`, reusing + incrementally persisting pages.

    ``generation_kwargs`` are forwarded verbatim to ``run_generation``; callers
    must not pass ``prior_pages`` or ``on_page_ready`` (this wrapper owns both).
    Returns the generated pages exactly as ``run_generation`` would.
    """
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        load_prior_pages,
        upsert_repository,
    )
    from repowise.core.persistence.crud import upsert_page_from_generated
    from repowise.core.pipeline import run_generation

    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    await init_db(engine)
    sf = create_session_factory(engine)

    async with get_session(sf) as session:
        repo_id = (
            await upsert_repository(session, name=repo_name, local_path=str(repo_path))
        ).id

    prior_pages: dict[str, Any] = {}
    if reuse_prior_pages:
        try:
            async with get_session(sf) as session:
                prior_pages = await load_prior_pages(session, repo_id)
            if prior_pages:
                logger.info("generation.prior_pages_loaded", count=len(prior_pages))
        except Exception as exc:
            logger.debug("generation.prior_pages_load_failed", error=str(exc))
            prior_pages = {}

    # A bounded handoff queue decouples the synchronous on_page_ready callback
    # (fired inside the generation loop) from the async DB writes, so a slow
    # write never stalls page generation. A sentinel closes the consumer.
    queue: asyncio.Queue[Any] = asyncio.Queue()
    sentinel = object()
    saved = 0

    async def _consumer() -> None:
        nonlocal saved
        while True:
            page = await queue.get()
            try:
                if page is sentinel:
                    return
                try:
                    async with get_session(sf) as session:
                        await upsert_page_from_generated(session, page, repo_id)
                    saved += 1
                except Exception as exc:
                    logger.debug(
                        "generation.incremental_persist_failed",
                        page_id=getattr(page, "page_id", "?"),
                        error=str(exc),
                    )
            finally:
                queue.task_done()

    consumer_task = asyncio.create_task(_consumer())

    def _on_page_ready(page: Any) -> None:
        # Synchronous, called from the generation loop — must never raise.
        with contextlib.suppress(Exception):
            queue.put_nowait(page)

    try:
        pages = await run_generation(
            repo_path=repo_path,
            **generation_kwargs,
            prior_pages=prior_pages,
            on_page_ready=_on_page_ready,
        )
    finally:
        await queue.put(sentinel)
        try:
            await asyncio.wait_for(consumer_task, timeout=_DRAIN_TIMEOUT_SECS)
        except TimeoutError:
            logger.warning("generation.persist_drain_timeout", saved=saved)
            consumer_task.cancel()
        except Exception as exc:
            logger.debug("generation.persist_consumer_error", error=str(exc))
        await engine.dispose()

    if saved:
        logger.info("generation.incremental_persist_done", pages_flushed=saved)
    return pages
