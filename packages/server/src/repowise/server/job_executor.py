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
        # Time-based throttling: hold off issuing a new write while one is
        # already in flight or one fired in the last second. Without this,
        # a tight phase (e.g. 1000 fast items) would create N concurrent
        # writes that all contend with the main pipeline's bulk persist
        # transaction, producing "database is locked" errors.
        self._min_write_interval_s = 1.0
        self._last_write_at: float = 0.0
        self._inflight: bool = False

    def on_phase_start(self, phase: str, total: int | None) -> None:
        self._phase = phase
        # Reset per-phase counters so the bar shows progress within the current phase
        self._completed = 0
        self._total = total
        self._pending_flush = 0
        # Force a write at phase boundaries so the UI label updates promptly
        # even if a throttled write was just issued.
        self._sync_job_status(force=True)
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

    def _sync_job_status(self, *, force: bool = False) -> None:
        """Fire-and-forget progress update in the current event loop.

        Tracks task references to allow cancellation before final status.
        Throttled: skipped if another write is already in flight, or if the
        last write was less than ``_min_write_interval_s`` ago — unless
        ``force=True`` (used at phase boundaries).
        """
        if self._stopped:
            return

        if not force:
            if self._inflight:
                return
            now = time.monotonic()
            if now - self._last_write_at < self._min_write_interval_s:
                return

        try:
            loop = asyncio.get_running_loop()
            self._inflight = True
            self._last_write_at = time.monotonic()
            t = loop.create_task(self._async_update())
            self._pending_tasks.add(t)

            def _on_done(task: asyncio.Task) -> None:
                self._pending_tasks.discard(task)
                self._inflight = False

            t.add_done_callback(_on_done)
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
        except Exception as exc:
            # Lock contention with the main pipeline transaction is recoverable —
            # the next throttled write will pick up the latest counts. Log a
            # brief one-liner instead of a multi-page traceback.
            msg = str(exc)
            if "database is locked" in msg or "OperationalError" in type(exc).__name__:
                logger.debug(
                    "progress_update_skipped_locked",
                    job_id=self._job_id,
                    phase=self._phase,
                )
            else:
                logger.debug("progress_update_failed", job_id=self._job_id, exc_info=True)


async def execute_job(
    job_id: str,
    app_state: Any,
    session_factory_override: Any = None,
) -> None:
    """Execute a pending pipeline job in the background.

    This is the single entry point called by the endpoint via
    ``asyncio.create_task()``.  It:

    1. Marks the job as ``running``
    2. Resolves the LLM provider from server config
    3. Runs ``run_pipeline()``
    4. Persists all results via ``persist_pipeline_result()``
    5. Marks the job as ``completed`` (or ``failed`` on error)

    In workspace mode, each repo has its own ``wiki.db`` and the route
    handler that created this job committed it to a per-repo session
    factory (``app_state.workspace_sessions[repo_id]``), not the primary
    one. The caller must pass that same factory in
    ``session_factory_override`` so we read from the same database — else
    we'd see "job_not_found" and the row would stay pending forever.
    """
    start = time.monotonic()
    progress: JobProgressCallback | None = None
    session_factory = None

    try:
        # Resolve required app_state attributes inside the try block so a
        # missing attribute (e.g., partially-initialised app_state during
        # development hot-reload) gets recorded as a job failure instead of
        # leaving the row stuck in 'pending' forever.
        session_factory = session_factory_override or app_state.session_factory
        fts = app_state.fts
        vector_store = app_state.vector_store

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

        # ---- Incremental page regeneration for sync mode ------------------
        # Sync runs run_pipeline(generate_docs=False) for the full index,
        # then regenerates only the wiki pages affected by recent changes.
        # This keeps docs fresh without the cost of a full re-index.
        incremental_pages: list = []
        if not is_full_resync and llm_client is not None:
            incremental_pages = await _incremental_page_regen(
                Path(repo_path), result, llm_client, config, progress,
            )

        # ---- Persist results -----------------------------------------------
        async with get_session(session_factory) as session:
            await persist_pipeline_result(result, session, repo_id)

            # Persist incrementally regenerated pages
            if incremental_pages:
                from repowise.core.persistence import upsert_page_from_generated

                for page in incremental_pages:
                    await upsert_page_from_generated(session, page, repo_id)

        # FTS indexing runs after session closes to avoid SQLite write conflicts
        all_pages = (result.generated_pages or []) + incremental_pages
        if fts is not None and all_pages:
            for page in all_pages:
                await fts.index(page.page_id, page.title, page.content)

        # ---- Mark completed ------------------------------------------------
        # Stop progress updates before writing final status to prevent a
        # late "running" update from overwriting "completed".
        await progress.drain_and_stop()

        elapsed = time.monotonic() - start
        total_input = sum(p.input_tokens for p in all_pages)
        total_output = sum(p.output_tokens for p in all_pages)
        pages_generated = len(all_pages)

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
                    "pages_generated": pages_generated,
                }
            )
            if job is not None:
                job.config_json = json.dumps(final_config)

            await update_job_status(
                session,
                job_id,
                "completed",
                completed_pages=pages_generated if pages_generated else result.file_count,
                total_pages=pages_generated if pages_generated else result.file_count,
            )

        # Update state.json so CLI incremental updates know the new baseline
        try:
            _state_path = Path(repo_path) / ".repowise" / "state.json"
            import subprocess as _sp

            _head_result = _sp.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if _head_result.returncode == 0:
                _head_sha = _head_result.stdout.strip()
                _state_data: dict = {}
                if _state_path.is_file():
                    _state_data = json.loads(_state_path.read_text(encoding="utf-8"))
                _state_data["last_sync_commit"] = _head_sha
                _state_path.parent.mkdir(parents=True, exist_ok=True)
                _state_path.write_text(json.dumps(_state_data, indent=2), encoding="utf-8")
        except Exception:
            logger.debug("state_json_update_failed", job_id=job_id, exc_info=True)

        # Hot-reload cross-repo enricher if available (workspace mode)
        try:
            enricher = getattr(app_state, "cross_repo_enricher", None)
            if enricher is not None and hasattr(enricher, "reload"):
                enricher.reload()
        except Exception:
            logger.debug("enricher_reload_failed", job_id=job_id, exc_info=True)

        logger.info(
            "job_completed",
            job_id=job_id,
            elapsed=round(elapsed, 1),
            files=result.file_count,
            symbols=result.symbol_count,
            pages=pages_generated,
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
        # If we failed before resolving session_factory, fall back to the one
        # on app_state (best-effort) so the row never stays stuck in pending.
        recovery_factory = session_factory or getattr(app_state, "session_factory", None)
        if recovery_factory is not None:
            try:
                async with get_session(recovery_factory) as session:
                    await update_job_status(
                        session,
                        job_id,
                        "failed",
                        error_message=str(exc)[:500],
                    )
            except Exception:
                logger.exception("job_status_update_failed", job_id=job_id)
        else:
            logger.error("job_status_update_skipped_no_session", job_id=job_id)


# ---------------------------------------------------------------------------
# Incremental page regeneration helper
# ---------------------------------------------------------------------------


async def _incremental_page_regen(
    repo_path: Path,
    result: Any,
    llm_client: Any,
    job_config: dict,
    progress: Any | None,
) -> list:
    """Regenerate only wiki pages affected by recent changes.

    Uses the graph from the just-completed pipeline run + git diff to detect
    which pages need updating.  Returns a list of GeneratedPage objects (may
    be empty if nothing changed or no base ref is available).
    """
    try:
        # Read base ref from state.json (the commit we last synced to)
        state_path = repo_path / ".repowise" / "state.json"
        base_ref: str | None = None
        if state_path.is_file():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            base_ref = state.get("last_sync_commit")

        # Webhook jobs may carry explicit before/after refs
        if not base_ref:
            base_ref = job_config.get("before")

        if not base_ref:
            logger.info("incremental_page_regen_skipped", reason="no_base_ref")
            return []

        import subprocess as _sp

        head_result = _sp.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if head_result.returncode != 0:
            return []
        head = head_result.stdout.strip()

        if head == base_ref:
            logger.info("incremental_page_regen_skipped", reason="no_new_commits")
            return []

        from repowise.core.ingestion import ChangeDetector
        from repowise.core.ingestion.change_detector import compute_adaptive_budget

        detector = ChangeDetector(repo_path)
        file_diffs = detector.get_changed_files(base_ref, head)
        if not file_diffs:
            return []

        cascade_budget = compute_adaptive_budget(file_diffs, result.file_count)
        affected = detector.get_affected_pages(file_diffs, result.graph_builder.graph(), cascade_budget)

        if not affected.regenerate:
            logger.info("incremental_page_regen_skipped", reason="no_affected_pages")
            return []

        logger.info(
            "incremental_page_regen_start",
            changed_files=len(file_diffs),
            affected_pages=len(affected.regenerate),
            cascade_budget=cascade_budget,
        )

        if progress:
            progress.on_phase_start("generation", len(affected.regenerate))

        # Filter parsed files to only affected ones
        regen_set = set(affected.regenerate)
        affected_parsed = [pf for pf in result.parsed_files if pf.file_info.path in regen_set]
        affected_source = {p: s for p, s in result.source_map.items() if p in regen_set}

        from repowise.core.generation import ContextAssembler, GenerationConfig, PageGenerator

        config = GenerationConfig()
        assembler = ContextAssembler(config)
        generator = PageGenerator(llm_client, assembler, config)

        pages = await generator.generate_all(
            affected_parsed,
            affected_source,
            result.graph_builder,
            result.repo_structure,
            result.repo_name,
            git_meta_map=result.git_meta_map,
        )

        logger.info("incremental_page_regen_done", pages=len(pages))
        return pages

    except Exception as exc:
        logger.warning("incremental_page_regen_failed", error=str(exc))
        return []
