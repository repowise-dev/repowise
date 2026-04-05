"""Background job executor for server-triggered pipeline runs.

Bridges the gap between the REST endpoints (which create pending jobs)
and the core pipeline (which does the actual work).  Uses the same
``run_pipeline()`` and ``persist_pipeline_result()`` as the CLI — zero
duplication.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import structlog

from repowise.core.persistence.crud import (
    get_generation_job,
    get_repository,
    update_job_status,
)
from repowise.core.persistence.database import get_session
from repowise.core.pipeline import persist_pipeline_result, run_pipeline

logger = structlog.get_logger(__name__)

# Phase → numeric level mapping for job.current_level
_PHASE_LEVELS = {
    "traverse": 0,
    "parse": 0,
    "graph": 0,
    "git": 0,
    "co_change": 0,
    "dead_code": 1,
    "decisions": 1,
    "generation": 2,
}


class JobProgressCallback:
    """ProgressCallback that writes progress to the GenerationJob record.

    The SSE stream endpoint polls the job table, so updating the record
    is sufficient to push live progress to the frontend.
    """

    def __init__(self, job_id: str, session_factory: Any) -> None:
        self._job_id = job_id
        self._session_factory = session_factory
        self._completed = 0
        self._total: int | None = None
        self._phase = ""
        self._pending_flush = 0
        self._stopped = False
        # Track in-flight update tasks to cancel before final status write
        self._pending_tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]
        # Batch DB writes: flush every N items to avoid per-item overhead
        self._flush_interval = 5

    def on_phase_start(self, phase: str, total: int | None) -> None:
        self._phase = phase
        # Reset per-phase counters so the bar shows progress within the current phase
        self._completed = 0
        self._total = total
        self._pending_flush = 0
        self._sync_job_status()  # Flush immediately so the label updates
        logger.info("job_phase_start", job_id=self._job_id, phase=phase, total=total)

    def on_item_done(self, phase: str) -> None:
        self._completed += 1
        self._pending_flush += 1
        if self._pending_flush >= self._flush_interval:
            self._pending_flush = 0
            self._sync_job_status()

    def on_message(self, level: str, text: str) -> None:
        getattr(logger, level, logger.info)(
            text, job_id=self._job_id, phase=self._phase
        )

    def _sync_job_status(self) -> None:
        """Fire-and-forget progress update in the current event loop.

        Tracks task references to allow cancellation before final status.
        """
        if self._stopped:
            return

        try:
            loop = asyncio.get_running_loop()
            t = loop.create_task(self._async_update())
            self._pending_tasks.add(t)
            t.add_done_callback(self._pending_tasks.discard)
        except RuntimeError:
            pass  # No event loop — skip the update

    async def drain_and_stop(self) -> None:
        """Wait for in-flight progress updates to finish, then prevent new ones.

        Must be called before writing the final job status to avoid a race
        where a late progress update overwrites ``completed`` with ``running``.

        We do NOT cancel tasks — a cancelled task whose DB write is already
        past the ``await`` will leave the session in a dirty state.  Instead
        we set the stopped flag (preventing new tasks) and let existing ones
        finish naturally.
        """
        self._stopped = True
        if self._pending_tasks:
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)
        self._pending_tasks.clear()

    async def _async_update(self) -> None:
        try:
            async with get_session(self._session_factory) as session:
                await update_job_status(
                    session,
                    self._job_id,
                    "running",
                    completed_pages=self._completed,
                    total_pages=self._total,
                    current_level=_PHASE_LEVELS.get(self._phase, 0),
                )
        except Exception:
            logger.debug("progress_update_failed", job_id=self._job_id, exc_info=True)


async def execute_job(job_id: str, app_state: Any) -> None:
    """Execute a pending pipeline job in the background.

    This is the single entry point called by the endpoint via
    ``asyncio.create_task()``.  It:

    1. Marks the job as ``running``
    2. Resolves the LLM provider from server config
    3. Runs ``run_pipeline()``
    4. Persists all results via ``persist_pipeline_result()``
    5. Marks the job as ``completed`` (or ``failed`` on error)
    """
    session_factory = app_state.session_factory
    fts = app_state.fts
    vector_store = app_state.vector_store
    start = time.monotonic()
    progress: JobProgressCallback | None = None

    try:
        # ---- Fetch job + repo metadata ------------------------------------
        async with get_session(session_factory) as session:
            job = await get_generation_job(session, job_id)
            if job is None:
                logger.error("job_not_found", job_id=job_id)
                return

            repo = await get_repository(session, job.repository_id)
            if repo is None:
                logger.error("repo_not_found", job_id=job_id, repo_id=job.repository_id)
                await update_job_status(session, job_id, "failed", error_message="Repository not found")
                return

            repo_path = repo.local_path
            repo_id = repo.id
            config = json.loads(job.config_json) if job.config_json else {}
            is_full_resync = config.get("mode") == "full_resync"

            # Mark running
            await update_job_status(session, job_id, "running")

        logger.info(
            "job_started",
            job_id=job_id,
            repo_path=repo_path,
            mode="full_resync" if is_full_resync else "sync",
        )

        # ---- Resolve LLM provider -----------------------------------------
        llm_client = None
        try:
            from repowise.server.provider_config import get_chat_provider_instance

            llm_client = get_chat_provider_instance()
        except Exception as exc:
            logger.warning("no_provider_configured", error=str(exc))
            # Continue without LLM — ingestion + analysis still work

        # ---- Run pipeline --------------------------------------------------
        progress = JobProgressCallback(job_id, session_factory)

        result = await run_pipeline(
            Path(repo_path),
            generate_docs=is_full_resync and llm_client is not None,
            llm_client=llm_client,
            vector_store=vector_store,
            progress=progress,
        )

        # ---- Persist results -----------------------------------------------
        async with get_session(session_factory) as session:
            await persist_pipeline_result(result, session, repo_id)

        # FTS indexing runs after session closes to avoid SQLite write conflicts
        if fts is not None and result.generated_pages:
            for page in result.generated_pages:
                await fts.index(page.page_id, page.title, page.content)

        # ---- Mark completed ------------------------------------------------
        # Stop progress updates before writing final status to prevent a
        # late "running" update from overwriting "completed".
        await progress.drain_and_stop()

        elapsed = time.monotonic() - start
        total_input = sum(p.input_tokens for p in (result.generated_pages or []))
        total_output = sum(p.output_tokens for p in (result.generated_pages or []))

        async with get_session(session_factory) as session:
            job = await get_generation_job(session, job_id)
            # Store summary in config for the frontend to display
            final_config = config.copy()
            final_config.update(
                {
                    "total_input_tokens": total_input,
                    "total_output_tokens": total_output,
                    "elapsed_seconds": round(elapsed, 1),
                    "file_count": result.file_count,
                    "symbol_count": result.symbol_count,
                    "pages_generated": len(result.generated_pages) if result.generated_pages else 0,
                }
            )
            if job is not None:
                job.config_json = json.dumps(final_config)

            await update_job_status(
                session,
                job_id,
                "completed",
                completed_pages=len(result.generated_pages) if result.generated_pages else result.file_count,
                total_pages=len(result.generated_pages) if result.generated_pages else result.file_count,
            )

        logger.info(
            "job_completed",
            job_id=job_id,
            elapsed=round(elapsed, 1),
            files=result.file_count,
            symbols=result.symbol_count,
            pages=len(result.generated_pages) if result.generated_pages else 0,
        )

    except Exception as exc:
        logger.exception("job_failed", job_id=job_id, error=str(exc))
        # Drain progress updates before writing final "failed" status to prevent
        # a late fire-and-forget progress update from overwriting it with "running".
        if progress is not None:
            try:
                await progress.drain_and_stop()
            except Exception:
                logger.debug("drain_failed_on_error_path", job_id=job_id, exc_info=True)
        try:
            async with get_session(session_factory) as session:
                await update_job_status(
                    session,
                    job_id,
                    "failed",
                    error_message=str(exc)[:500],
                )
        except Exception:
            logger.exception("job_status_update_failed", job_id=job_id)
