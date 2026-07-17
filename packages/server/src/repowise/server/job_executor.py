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

from repowise.core.cancellation import (
    CancellationToken,
    PipelineCancelled,
    get_active_token,
    set_active_token,
)
from repowise.core.persistence.crud import (
    get_generation_job,
    get_repository,
    update_job_status,
)
from repowise.core.persistence.database import get_session
from repowise.core.pipeline import persist_pipeline_result, run_pipeline
from repowise.server.job_events import JobEventBuffer, create_event_buffer

logger = structlog.get_logger(__name__)


def _repo_exclude_patterns(repo: Any, repo_path: str) -> list[str]:
    """Collect a server job's exclude patterns from both config sources.

    Web-managed repos store settings in ``Repository.settings_json``; CLI and
    ``repowise init`` workflows write them to ``.repowise/config.yaml``. Server
    jobs should honor either, so we merge both — order-preserving and
    de-duplicated, settings first. A missing or malformed source is ignored
    rather than fatal.
    """
    patterns: list[str] = []

    def _add(values: Any) -> None:
        if not isinstance(values, list):
            return
        for value in values:
            if isinstance(value, str) and value not in patterns:
                patterns.append(value)

    # Source 1: DB-stored repo settings (web UI).
    try:
        settings = json.loads(getattr(repo, "settings_json", "") or "{}")
        if isinstance(settings, dict):
            _add(settings.get("exclude_patterns"))
    except (TypeError, ValueError):
        logger.debug("repo_settings_json_unparsable", repo_path=repo_path)

    # Source 2: repo-local .repowise/config.yaml (CLI/init). Reuse the shared
    # loader so we inherit its YAML + flat-format fallback handling.
    try:
        from repowise.core.repo_config import load_repo_config

        cfg = load_repo_config(Path(repo_path))
        if isinstance(cfg, dict):
            _add(cfg.get("exclude_patterns"))
    except Exception:
        logger.debug("repo_config_yaml_unreadable", repo_path=repo_path)

    return patterns


def _repo_wiki_style(repo: Any, repo_path: str) -> str:
    """Resolve a server job's effective wiki style from both config sources.

    Web-managed repos store the style in ``Repository.settings_json`` (set via the
    PATCH endpoint); CLI/``repowise init`` write it to ``.repowise/config.yaml``.
    Settings take precedence (the web UI is the more deliberate, recent signal),
    then config.yaml, then the default. Unknown values resolve to the default
    rather than failing the job.
    """
    from repowise.core.generation.styles import resolve_style

    style: str | None = None
    try:
        settings = json.loads(getattr(repo, "settings_json", "") or "{}")
        if isinstance(settings, dict):
            style = settings.get("wiki_style")
    except (TypeError, ValueError):
        logger.debug("repo_settings_json_unparsable", repo_path=repo_path)

    if not style:
        try:
            from repowise.core.repo_config import load_repo_config

            cfg = load_repo_config(Path(repo_path))
            if isinstance(cfg, dict):
                style = cfg.get("wiki_style")
        except Exception:
            logger.debug("repo_config_yaml_unreadable", repo_path=repo_path)

    return resolve_style(style, repo_path=repo_path).name


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

    def __init__(
        self,
        job_id: str,
        session_factory: Any,
        events: JobEventBuffer | None = None,
    ) -> None:
        self._job_id = job_id
        self._session_factory = session_factory
        self._events = events
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
        if self._events is not None:
            self._events.set_phase(phase, total)
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
        if self._events is not None:
            self._events.add(level, text)
        getattr(logger, level, logger.info)(text, job_id=self._job_id, phase=self._phase)

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


def get_cancel_tokens(app_state: Any) -> dict[str, CancellationToken]:
    """Per-job cancellation tokens, keyed by job id (created on demand)."""
    tokens = getattr(app_state, "job_cancel_tokens", None)
    if tokens is None:
        tokens = {}
        app_state.job_cancel_tokens = tokens
    return tokens


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
    5. Marks the job as ``completed`` (or ``failed`` on error,
       ``cancelled`` on a user cancel)

    Job modes (``config_json.mode``): ``sync`` (default) indexes then
    regenerates only changed pages; ``full_resync`` regenerates all docs;
    ``initial_index`` is the first-ever index of a repo triggered from the
    API — full pipeline with docs plus the ``state.json``/``config.yaml``
    baseline the CLI writes at ``repowise init``; ``index_only`` refreshes
    the index/analysis without any LLM work.

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

    # Cooperative cancellation: the cancel endpoint flips this token (which
    # unwinds the CPU-bound loops that poll check_cancelled) and cancels the
    # asyncio task (which interrupts the awaits in between). The core token
    # slot is a process global, so when two jobs overlap the later one's token
    # occupies it and the earlier job's CPU loops poll the wrong token — its
    # async awaits still cancel, only an in-flight to_thread worker runs on.
    # Acceptable for the single-active-job norm; worker isolation is the
    # upgrade path.
    cancel_token = CancellationToken()
    get_cancel_tokens(app_state)[job_id] = cancel_token
    set_active_token(cancel_token)

    try:
        # Resolve required app_state attributes inside the try block so a
        # missing attribute (e.g., partially-initialised app_state during
        # development hot-reload) gets recorded as a job failure instead of
        # leaving the row stuck in 'pending' forever.
        session_factory = session_factory_override or app_state.session_factory
        fts = app_state.fts

        # ---- Fetch job + repo metadata ------------------------------------
        async with get_session(session_factory) as session:
            job = await get_generation_job(session, job_id)
            if job is None:
                logger.error("job_not_found", job_id=job_id)
                return

            repo = await get_repository(session, job.repository_id)
            if repo is None:
                logger.error("repo_not_found", job_id=job_id, repo_id=job.repository_id)
                await update_job_status(
                    session, job_id, "failed", error_message="Repository not found"
                )
                return

            repo_path = repo.local_path
            repo_id = repo.id
            # Resolve excludes while ``repo`` is still session-attached. Every
            # job entry point flows through here, so this covers them all.
            exclude_patterns = _repo_exclude_patterns(repo, repo_path)
            # Resolve the wiki style while ``repo`` is still session-attached.
            wiki_style = _repo_wiki_style(repo, repo_path)
            config = json.loads(job.config_json) if job.config_json else {}
            mode = config.get("mode") or "sync"
            is_full_resync = mode == "full_resync"
            is_initial_index = mode == "initial_index"
            is_index_only = mode == "index_only"

            # Mark running
            await update_job_status(session, job_id, "running")

        # Vector writes and deletes must follow the repository, just like its
        # routed SQL session. This opens/creates <repo>/.repowise/lancedb and
        # caches it by repo id; the global primary store is only a fallback for
        # partially initialized development app states.
        from repowise.server.search_helpers import resolve_repo_vector_store

        vector_store = await resolve_repo_vector_store(
            app_state,
            repo_id,
            repo_path=repo_path,
            create=True,
        )
        if vector_store is None:
            vector_store = app_state.vector_store

        logger.info("job_started", job_id=job_id, repo_path=repo_path, mode=mode)

        # ---- Resolve LLM provider -----------------------------------------
        llm_client = None
        docs_skip_reason: str | None = None
        if not is_index_only:
            try:
                from repowise.server.provider_config import get_chat_provider_instance

                # Pass the repo path so the job reuses the provider/model/key the
                # repo was configured with (``.repowise/config.yaml`` + ``.env``)
                # rather than the server-global default.
                llm_client = get_chat_provider_instance(repo_path=repo_path)
            except Exception as exc:
                docs_skip_reason = f"no provider configured: {exc}"
                logger.warning("no_provider_configured", error=str(exc))
                # Continue without LLM — ingestion + analysis still work

        # ---- Run pipeline --------------------------------------------------
        events = create_event_buffer(app_state, job_id)
        progress = JobProgressCallback(job_id, session_factory, events)

        generate_docs = (
            (is_full_resync or is_initial_index)
            and llm_client is not None
            and bool(config.get("generate_docs", True))
        )
        if is_initial_index:
            # First index of an API-registered repo: make sure the repo-local
            # data directory exists before the pipeline writes artifacts.
            (Path(repo_path) / ".repowise").mkdir(parents=True, exist_ok=True)
            if llm_client is None and config.get("generate_docs", True):
                progress.on_message(
                    "warning",
                    "No LLM provider configured; indexing without documentation "
                    "generation. Configure a provider and run a full resync to "
                    "generate docs.",
                )

        result = await run_pipeline(
            Path(repo_path),
            generate_docs=generate_docs,
            llm_client=llm_client,
            vector_store=vector_store,
            progress=progress,
            exclude_patterns=exclude_patterns or None,
            wiki_style=wiki_style,
        )

        # ---- Incremental page regeneration for sync mode ------------------
        # Sync runs run_pipeline(generate_docs=False) for the full index,
        # then regenerates only the wiki pages affected by recent changes.
        # This keeps docs fresh without the cost of a full re-index.
        incremental_pages: list = []
        if mode == "sync" and llm_client is not None:
            incremental_pages = await _incremental_page_regen(
                Path(repo_path),
                result,
                llm_client,
                config,
                progress,
                repo_wiki_style=wiki_style,
            )

        # ---- Persist results -----------------------------------------------
        async with get_session(session_factory) as session:
            swept_page_ids = await persist_pipeline_result(result, session, repo_id)

            # Persist incrementally regenerated pages
            if incremental_pages:
                from repowise.core.persistence import upsert_page_from_generated

                for page in incremental_pages:
                    await upsert_page_from_generated(session, page, repo_id)

            # Drop swept pages from the vector store *before* the SQL session
            # commits. The vector store is a separate engine/file (pgvector DB,
            # LanceDB dir, or in-memory), so there is no SQLite write-lock
            # conflict and the idempotent delete leaves the durable SQL commit
            # last: an interrupted run self-heals (embedding already gone, SQL
            # rows follow on commit).
            if swept_page_ids and vector_store is not None:
                await vector_store.delete_many(swept_page_ids)

        # FTS deletes/indexing run after the session closes: the FTS index can
        # share the SQLite file with the session, so writing it while the
        # session holds a write lock raises "database is locked". The swept-id
        # delete is idempotent (orphan FTS rows only) and must stay here.
        all_pages = (result.generated_pages or []) + incremental_pages
        if fts is not None and swept_page_ids:
            await fts.delete_many(swept_page_ids)
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

        # Update state.json so CLI incremental updates know the new baseline.
        # An initial index also persists the full baseline (docs flags, run
        # mode, config) that `repowise init` would have written.
        try:
            if is_initial_index:
                _persist_initial_index_state(
                    Path(repo_path),
                    llm_client=llm_client,
                    docs_enabled=generate_docs,
                    docs_skip_reason=docs_skip_reason,
                    total_pages=len(all_pages),
                    wiki_style=wiki_style,
                    exclude_patterns=exclude_patterns,
                )
            else:
                _stamp_last_sync_commit(Path(repo_path))
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

    except (PipelineCancelled, asyncio.CancelledError):
        # User-requested cancel: the endpoint flipped our token and/or
        # cancelled the task. Record the terminal state and swallow — this is
        # the top of a background task, nothing above us awaits the result.
        logger.info("job_cancelled", job_id=job_id)
        await _finalize_job_status(
            app_state,
            session_factory,
            progress,
            job_id,
            status="cancelled",
            error_message="Cancelled by user",
        )
    except Exception as exc:
        logger.exception("job_failed", job_id=job_id, error=str(exc))
        await _finalize_job_status(
            app_state,
            session_factory,
            progress,
            job_id,
            status="failed",
            error_message=str(exc)[:500],
        )
    finally:
        get_cancel_tokens(app_state).pop(job_id, None)
        # Disarm only if the global slot still holds our token; a later job
        # may have replaced it, and its token must not be clobbered. Reset to
        # None rather than the captured previous token — that one may belong
        # to a job that already finished (possibly cancelled), and re-arming
        # it would poison the next check_cancelled() poll.
        if get_active_token() is cancel_token:
            set_active_token(None)


async def _finalize_job_status(
    app_state: Any,
    session_factory: Any,
    progress: JobProgressCallback | None,
    job_id: str,
    *,
    status: str,
    error_message: str,
) -> None:
    """Best-effort terminal status write shared by the failure and cancel paths.

    Drains in-flight progress updates first so a late fire-and-forget write
    can't overwrite the terminal status with "running". Falls back to the
    app-level session factory when the job's own factory was never resolved,
    so the row never stays stuck in pending.
    """
    if progress is not None:
        try:
            await progress.drain_and_stop()
        except Exception:
            logger.debug("drain_failed_on_error_path", job_id=job_id, exc_info=True)
    recovery_factory = session_factory or getattr(app_state, "session_factory", None)
    if recovery_factory is None:
        logger.error("job_status_update_skipped_no_session", job_id=job_id)
        return
    try:
        async with get_session(recovery_factory) as session:
            await update_job_status(
                session,
                job_id,
                status,
                error_message=error_message,
            )
    except Exception:
        logger.exception("job_status_update_failed", job_id=job_id)


# ---------------------------------------------------------------------------
# state.json / config.yaml writers
# ---------------------------------------------------------------------------


def _read_head_sha(repo_path: Path) -> str | None:
    import subprocess as _sp

    try:
        result = _sp.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _load_state(repo_path: Path) -> dict:
    state_path = repo_path / ".repowise" / "state.json"
    if state_path.is_file():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {}


def _save_state(repo_path: Path, state: dict) -> None:
    state_path = repo_path / ".repowise" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _stamp_last_sync_commit(repo_path: Path) -> None:
    """Record the synced HEAD so CLI incremental updates know the baseline."""
    head = _read_head_sha(repo_path)
    if not head:
        return
    state = _load_state(repo_path)
    state["last_sync_commit"] = head
    _save_state(repo_path, state)


def _persist_initial_index_state(
    repo_path: Path,
    *,
    llm_client: Any,
    docs_enabled: bool,
    docs_skip_reason: str | None,
    total_pages: int,
    wiki_style: str,
    exclude_patterns: list[str],
) -> None:
    """Write the ``repowise init`` baseline after a first API-driven index.

    Mirrors what the CLI persists at the end of ``init``: a complete
    ``state.json`` (sync baseline, docs flags, run mode, store-format stamp,
    config fingerprint) and a ``config.yaml`` recording the provider/model
    and style the run used, so later CLI ``update`` runs and server jobs
    resolve the same configuration.
    """
    from repowise.core.generation.styles import DEFAULT_STYLE
    from repowise.core.repo_config import (
        config_fingerprint,
        load_repo_config,
        save_repo_config,
    )

    # ---- config.yaml (written first: the fingerprint below covers it) ----
    config = load_repo_config(repo_path)
    if llm_client is not None:
        config["provider"] = getattr(llm_client, "provider_name", "") or config.get("provider")
        config["model"] = getattr(llm_client, "model_name", "") or config.get("model")
    if wiki_style and wiki_style != DEFAULT_STYLE and not config.get("wiki_style"):
        config["wiki_style"] = wiki_style
    if exclude_patterns and not config.get("exclude_patterns"):
        config["exclude_patterns"] = exclude_patterns
    try:
        save_repo_config(repo_path, config)
    except Exception:
        logger.debug("config_yaml_write_failed", repo_path=str(repo_path), exc_info=True)

    # ---- state.json ----
    state = _load_state(repo_path)
    head = _read_head_sha(repo_path)
    if head:
        state["last_sync_commit"] = head
    state["docs_enabled"] = docs_enabled
    if docs_skip_reason and not docs_enabled:
        state["docs_skip_reason"] = docs_skip_reason
    state["run_mode"] = "standard"
    state["git_tier"] = "full"
    state["include_submodules"] = False
    state["total_pages"] = total_pages
    if llm_client is not None:
        state["provider"] = getattr(llm_client, "provider_name", "")
        state["model"] = getattr(llm_client, "model_name", "")

    try:
        from importlib.metadata import version as _dist_version

        from repowise.core.upgrade import stamp as _stamp_store_version

        try:
            _pkg_version: str | None = _dist_version("repowise")
        except Exception:
            _pkg_version = None
        _stamp_store_version(state, package_version=_pkg_version)
    except Exception:
        logger.debug("store_version_stamp_failed", repo_path=str(repo_path), exc_info=True)

    state["config_fingerprint"] = config_fingerprint(repo_path)
    _save_state(repo_path, state)


# ---------------------------------------------------------------------------
# Incremental page regeneration helper
# ---------------------------------------------------------------------------


async def _incremental_page_regen(
    repo_path: Path,
    result: Any,
    llm_client: Any,
    job_config: dict,
    progress: Any | None,
    repo_wiki_style: str = "comprehensive",
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
        affected = detector.get_affected_pages(
            file_diffs,
            result.graph_builder.graph(),
            cascade_budget,
            pagerank=result.graph_builder.pagerank(),
        )

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

        # Effective style: a per-page override carried in the job config (set by
        # the regenerate endpoint, D10) wins over the repo's default style.
        from repowise.core.generation.styles import resolve_style
        from repowise.core.reasoning import resolve_reasoning
        from repowise.core.repo_config import load_repo_config

        effective_style = resolve_style(
            job_config.get("style") or repo_wiki_style, repo_path=repo_path
        ).name
        repo_cfg = load_repo_config(repo_path)
        generation_config = GenerationConfig(
            reasoning=resolve_reasoning(config=repo_cfg),
            wiki_style=effective_style,
            # Regenerate in the repo's configured output language, not default
            # English (PageGenerator picks the language up from the config).
            language=repo_cfg.get("language", "en"),
        )
        assembler = ContextAssembler(generation_config)
        generator = PageGenerator(llm_client, assembler, generation_config, repo_path=repo_path)

        pages = await generator.generate_all(
            affected_parsed,
            affected_source,
            result.graph_builder,
            result.repo_structure,
            result.repo_name,
            git_meta_map=result.git_meta_map,
            repo_path=repo_path,
        )

        logger.info("incremental_page_regen_done", pages=len(pages))
        return pages

    except Exception as exc:
        logger.warning("incremental_page_regen_failed", error=str(exc))
        return []
