"""Incremental, resume-friendly page generation for ``repowise init``.

The orchestrator's :func:`run_generation` buffers every page in memory and the
CLI writes them to the database only once, at the very end of the run, from
``_persist_result``. That is fine for a clean run but loses everything when a
long generation phase is interrupted: pages already embedded into the vector
store but never written to the ``pages`` table are lost from the wiki. A resume
counts a page as done only when it has both a vector and a stored row, so such
a page is regenerated rather than skipped.

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

# Pages written per transaction. The consumer only runs between generation's
# awaits, so pages pile up in the queue; one commit per page left a CPU-bound
# run draining thousands of single-row transactions after generation ended.
_FLUSH_BATCH = 200


async def run_generation_with_persistence(
    *,
    repo_path: Path,
    repo_name: str,
    reuse_prior_pages: bool = True,
    **generation_kwargs: Any,
) -> list[Any]:
    """Run :func:`run_generation`, reusing + incrementally persisting pages.

    ``generation_kwargs`` are forwarded verbatim to ``run_generation``; callers
    must not pass ``prior_pages``, ``on_page_ready`` or ``persisted_page_ids``
    (this wrapper owns them).
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
    from repowise.core.pipeline import run_generation, timed

    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    await init_db(engine)
    sf = create_session_factory(engine)

    async with get_session(sf) as session:
        repo_id = (await upsert_repository(session, name=repo_name, local_path=str(repo_path))).id

    resume = bool(generation_kwargs.get("resume"))
    prior_pages: dict[str, Any] = {}
    # On resume, the ids with a stored row: a vector without one is a page the
    # last run embedded but died before saving, so it must be regenerated.
    # None (load failed) falls back to trusting the vector store.
    persisted_page_ids: set[str] | None = None
    if reuse_prior_pages or resume:
        try:
            async with get_session(sf) as session:
                loaded = await load_prior_pages(session, repo_id)
            persisted_page_ids = set(loaded)
            if reuse_prior_pages:
                prior_pages = loaded
            if loaded:
                logger.info("generation.prior_pages_loaded", count=len(loaded))
        except Exception as exc:
            logger.debug("generation.prior_pages_load_failed", error=str(exc))

    # A bounded handoff queue decouples the synchronous on_page_ready callback
    # (fired inside the generation loop) from the async DB writes, so a slow
    # write never stalls page generation. A sentinel closes the consumer.
    queue: asyncio.Queue[Any] = asyncio.Queue()
    sentinel = object()
    saved = 0

    async def _write(pages: list[Any]) -> int:
        try:
            async with get_session(sf) as session:
                for page in pages:
                    await upsert_page_from_generated(session, page, repo_id)
            return len(pages)
        except Exception:
            # One bad page must not cost its batch: retry them one at a time.
            written = 0
            for page in pages:
                try:
                    async with get_session(sf) as session:
                        await upsert_page_from_generated(session, page, repo_id)
                    written += 1
                except Exception as exc:
                    logger.debug(
                        "generation.incremental_persist_failed",
                        page_id=getattr(page, "page_id", "?"),
                        error=str(exc),
                    )
            return written

    async def _consumer() -> None:
        nonlocal saved
        while True:
            batch = [await queue.get()]
            while len(batch) < _FLUSH_BATCH and not queue.empty():
                batch.append(queue.get_nowait())
            try:
                pages = [page for page in batch if page is not sentinel]
                if pages:
                    saved += await _write(pages)
                if len(pages) < len(batch):
                    return
            finally:
                for _ in batch:
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
            persisted_page_ids=persisted_page_ids if resume else None,
        )
    finally:
        await queue.put(sentinel)
        progress = generation_kwargs.get("progress")
        with timed(getattr(progress, "table", None), "generation.persist_drain"):
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
