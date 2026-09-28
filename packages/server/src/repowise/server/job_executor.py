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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from repowise.core.cancellation import (
    CancellationToken,
    PipelineCancelled,
    get_active_token,
    set_active_token,
)
from repowise.core.docs_mode import DocsMode
from repowise.core.persistence.crud import (
    get_generation_job,
    get_repository,
    update_job_status,
)
from repowise.core.persistence.database import get_session
from repowise.core.pipeline import persist_pipeline_result, run_pipeline
from repowise.core.pipeline.modes import OrchestratorMode
from repowise.server.job_events import JobEventBuffer, create_event_buffer

# The ``noqa: F401`` names live in the sibling job modules and are re-exported
# because the repos router and the tests import them from here.
from repowise.server.job_generate import (  # noqa: F401
    _build_generate_intent,
    _build_generation_config,
    _effective_cascade,
    _ranked_coverage_pct,
    _resolve_generate_scope,
    _run_generate_job,
)
from repowise.server.job_incremental import (
    _incremental_page_regen,
    _plan_incremental_page_regen,  # noqa: F401
)
from repowise.server.job_records import _complete_job_row, _finalize_job_status
from repowise.server.job_state import (
    _load_state,  # noqa: F401
    _persist_initial_index_state,
    _stamp_last_sync_commit,
)

logger = structlog.get_logger(__name__)


def _repo_exclude_patterns(repo: Any, repo_path: str) -> list[str]:
    """Collect a server job's exclude patterns from both config sources.

    ``Repository.settings_json`` (web UI) and ``.repowise/config.yaml`` (CLI),
    merged in order, de-duplicated, settings first. A missing or malformed
    source is ignored.
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

    # Source 2: repo-local .repowise/config.yaml (CLI/init), via the shared loader.
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

    ``Repository.settings_json`` wins (the web UI is the more deliberate
    signal), then ``.repowise/config.yaml``, then the default. Unknown values
    resolve to the default rather than failing the job.
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


# Job modes execute_job handles; "generate" and "single_page" go to _run_generate_job.
VALID_JOB_MODES: set[str] = {
    "sync",
    "full_resync",
    "initial_index",
    "index_only",
    "generate",
    "single_page",
}


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
        # At most one write in flight and one per second, or a fast phase
        # contends with the pipeline's persist ("database is locked").
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
        # Force a write so the UI's phase label updates promptly.
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

        Throttled unless ``force=True``: skipped while a write is in flight or
        within ``_min_write_interval_s`` of the last.
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

        Call before the final status write, or a late update can overwrite
        ``completed`` with ``running``. Tasks are not cancelled: one cancelled
        mid-write leaves the session dirty.
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
                    total_pages=self._total if self._total is not None else 0,
                    current_level=_PHASE_LEVELS.get(self._phase, 0),
                )
        except Exception as exc:
            # Lock contention is recoverable: the next write carries the counts.
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


@dataclass(frozen=True)
class _JobSpec:
    """What a job row and its repository ask for, read in one session."""

    job_id: str
    repo_id: str
    repo_path: str
    config: dict
    mode: str
    exclude_patterns: list[str]
    wiki_style: str

    @property
    def is_full_resync(self) -> bool:
        return self.mode == "full_resync"

    @property
    def is_initial_index(self) -> bool:
        return self.mode == "initial_index"

    @property
    def is_index_only(self) -> bool:
        return self.mode == "index_only"


@dataclass
class _JobRun:
    """A started job plus the resources every step of it writes through."""

    spec: _JobSpec
    session_factory: Any
    fts: Any
    vector_store: Any
    llm_client: Any
    docs_skip_reason: str | None
    progress: JobProgressCallback
    start: float


@dataclass(frozen=True)
class _DocsPlan:
    """Which wiki an index job writes: model prose, templates, or none."""

    llm: bool
    deterministic: bool

    @classmethod
    def for_job(cls, spec: _JobSpec, llm_client: Any) -> _DocsPlan:
        want_docs = bool(spec.config.get("generate_docs", True))
        return cls(
            # LLM docs: an initial index or full resync with a provider configured.
            llm=(
                (spec.is_full_resync or spec.is_initial_index)
                and llm_client is not None
                and want_docs
            ),
            # Keyless first index: a template wiki, as `repowise init --index-only`
            # renders. Initial index only, so a keyless full_resync never
            # overwrites a model-written wiki with templates.
            deterministic=spec.is_initial_index and llm_client is None and want_docs,
        )

    @property
    def generate(self) -> bool:
        return self.llm or self.deterministic

    @property
    def pipeline_mode(self) -> OrchestratorMode:
        return OrchestratorMode.DETERMINISTIC if self.deterministic else OrchestratorMode.STANDARD

    @property
    def docs_mode(self) -> DocsMode:
        return "llm" if self.llm else "deterministic" if self.deterministic else "none"


async def execute_job(
    job_id: str,
    app_state: Any,
    session_factory_override: Any = None,
) -> None:
    """Execute a pending pipeline job in the background.

    The single entry point the endpoint starts with ``asyncio.create_task()``.
    Marks the job running, runs and persists the pipeline, then marks it
    ``completed``, ``failed`` or ``cancelled``.

    Job modes (``config_json.mode``): ``sync`` (default) indexes then
    regenerates only changed pages; ``full_resync`` regenerates all docs;
    ``initial_index`` is a first API-triggered index, also writing the
    baseline ``repowise init`` writes; ``index_only`` does no LLM work.

    In workspace mode, pass the per-repo session factory the route committed
    the job to as ``session_factory_override``, or the job is never found.
    """
    start = time.monotonic()
    progress: JobProgressCallback | None = None
    session_factory = None

    # Cancel flips this token (for CPU loops) and cancels the task (for awaits).
    # The token slot is process-global, so with overlapping jobs the earlier
    # one's thread work runs on. Fine for one active job; isolate workers if not.
    cancel_token = CancellationToken()
    get_cancel_tokens(app_state)[job_id] = cancel_token
    set_active_token(cancel_token)

    try:
        # Inside the try, so a missing app_state attribute fails the job
        # instead of leaving it pending.
        session_factory = session_factory_override or app_state.session_factory
        fts = app_state.fts

        spec = await _start_job(session_factory, job_id)
        if spec is None:
            return
        vector_store = await _resolve_job_vector_store(app_state, spec)
        logger.info("job_started", job_id=job_id, repo_path=spec.repo_path, mode=spec.mode)
        llm_client, docs_skip_reason = _resolve_llm_client(spec)

        events = create_event_buffer(app_state, job_id)
        progress = JobProgressCallback(job_id, session_factory, events)
        run = _JobRun(
            spec=spec,
            session_factory=session_factory,
            fts=fts,
            vector_store=vector_store,
            llm_client=llm_client,
            docs_skip_reason=docs_skip_reason,
            progress=progress,
            start=start,
        )
        if spec.mode in ("generate", "single_page"):
            await _run_scoped_generation(run)
        else:
            await _run_index_job(run, app_state)

    except (PipelineCancelled, asyncio.CancelledError):
        # Record and swallow: nothing awaits a background task's result.
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
        # Disarm only our own token, and to None: a previous token may belong
        # to a finished (cancelled) job and would poison the next poll.
        if get_active_token() is cancel_token:
            set_active_token(None)


async def _start_job(session_factory: Any, job_id: str) -> _JobSpec | None:
    """Read the job and its repository, validate the mode, and mark it running.

    ``None`` means there is nothing to run; a missing repository is recorded
    as a failure on the job row first.
    """
    async with get_session(session_factory) as session:
        job = await get_generation_job(session, job_id)
        if job is None:
            logger.error("job_not_found", job_id=job_id)
            return None

        repo = await get_repository(session, job.repository_id)
        if repo is None:
            logger.error("repo_not_found", job_id=job_id, repo_id=job.repository_id)
            await update_job_status(
                session, job_id, "failed", error_message="Repository not found"
            )
            return None

        repo_path = repo.local_path
        config = json.loads(job.config_json) if job.config_json else {}
        mode = str(config.get("mode") or "sync")
        spec = _JobSpec(
            job_id=job_id,
            repo_id=repo.id,
            repo_path=repo_path,
            config=config,
            mode=mode,
            # Both resolved while ``repo`` is still session-attached.
            exclude_patterns=_repo_exclude_patterns(repo, repo_path),
            wiki_style=_repo_wiki_style(repo, repo_path),
        )
        if mode not in VALID_JOB_MODES:
            valid_str = ", ".join(sorted(VALID_JOB_MODES))
            raise ValueError(
                f"Invalid job mode '{mode}'. Expected one of: {valid_str}"
            )

        # Mark running
        await update_job_status(session, job_id, "running")
    return spec


async def _resolve_job_vector_store(app_state: Any, spec: _JobSpec) -> Any:
    """The repository's own vector store, like its routed SQL session.

    The global store is only a fallback for a partially initialized app state."""
    from repowise.server.search_helpers import resolve_repo_vector_store

    vector_store = await resolve_repo_vector_store(
        app_state,
        spec.repo_id,
        repo_path=spec.repo_path,
        create=True,
    )
    if vector_store is None:
        vector_store = app_state.vector_store
    return vector_store


def _resolve_llm_client(spec: _JobSpec) -> tuple[Any, str | None]:
    """The job's chat provider, or ``None`` and why docs will be skipped.

    Index-only jobs never ask for one. A job whose provider cannot be built
    continues without it: ingestion and analysis still work.
    """
    if spec.is_index_only:
        return None, None
    try:
        from repowise.server.provider_config import get_chat_provider_instance

        # The repo id reaches the UI picker's per-repo choice, which a
        # path-only resolve would skip for auto-detection.
        return (
            get_chat_provider_instance(repo_path=spec.repo_path, repo_id=spec.repo_id),
            None,
        )
    except Exception as exc:
        logger.warning("no_provider_configured", error=str(exc))
        return None, f"no provider configured: {exc}"


async def _run_scoped_generation(run: _JobRun) -> None:
    """`repowise generate` over HTTP: write an explicit subset of pages via
    the shared core engine, not a full re-index. `single_page` is the
    legacy per-page regenerate alias for the same path."""
    if run.llm_client is None:
        raise RuntimeError(
            run.docs_skip_reason or "No LLM provider is configured for this repository."
        )
    spec = run.spec
    await _run_generate_job(
        job_id=spec.job_id,
        repo_id=spec.repo_id,
        repo_path=Path(spec.repo_path),
        config=spec.config,
        session_factory=run.session_factory,
        provider=run.llm_client,
        vector_store=run.vector_store,
        fts=run.fts,
        progress=run.progress,
        exclude_patterns=spec.exclude_patterns or [],
        wiki_style=spec.wiki_style,
        start=run.start,
    )


async def _run_index_job(run: _JobRun, app_state: Any) -> None:
    """Index the repository, persist everything, and record the outcome."""
    spec = run.spec
    docs = _DocsPlan.for_job(spec, run.llm_client)
    result = await _run_index_pipeline(run, docs)
    incremental_pages = await _sync_incremental_pages(run, result)
    swept_page_ids = await _persist_index_result(run, result, incremental_pages)

    all_pages = (result.generated_pages or []) + incremental_pages
    await _refresh_fts(run.fts, swept_page_ids, all_pages)

    # Stop progress updates first, so none overwrites "completed".
    await run.progress.drain_and_stop()

    elapsed = time.monotonic() - run.start
    pages_generated = await _record_index_completion(run, result, all_pages, elapsed)
    await _write_index_state(run, docs, total_pages=len(all_pages))
    await _reload_cross_repo_enricher(app_state, spec.job_id)

    logger.info(
        "job_completed",
        job_id=spec.job_id,
        elapsed=round(elapsed, 1),
        files=result.file_count,
        symbols=result.symbol_count,
        pages=pages_generated,
    )


async def _run_index_pipeline(run: _JobRun, docs: _DocsPlan) -> Any:
    spec = run.spec
    if spec.is_initial_index:
        # First index of an API-registered repo: make sure the repo-local
        # data directory exists before the pipeline writes artifacts.
        (Path(spec.repo_path) / ".repowise").mkdir(parents=True, exist_ok=True)
        if docs.deterministic:
            run.progress.on_message(
                "info",
                "No LLM provider configured; rendering a template wiki from the "
                "code's structure (no model, no cost). Configure a provider and "
                "run a full resync to write the wiki with a model.",
            )

    return await run_pipeline(
        Path(spec.repo_path),
        generate_docs=docs.generate,
        llm_client=run.llm_client,
        vector_store=run.vector_store,
        progress=run.progress,
        exclude_patterns=spec.exclude_patterns or None,
        wiki_style=spec.wiki_style,
        mode=docs.pipeline_mode,
    )


async def _sync_incremental_pages(run: _JobRun, result: Any) -> list:
    """Incremental page regeneration for sync mode.

    Sync indexes without docs, then regenerates only the pages affected by
    recent changes.
    """
    spec = run.spec
    if spec.mode != "sync" or run.llm_client is None:
        return []
    # The vector store re-embeds re-rendered pages; prior pages let an
    # unchanged prompt reuse its page instead of re-billing.
    from repowise.core.persistence import load_prior_pages

    async with get_session(run.session_factory) as session:
        prior_pages = await load_prior_pages(session, spec.repo_id)
    return await _incremental_page_regen(
        Path(spec.repo_path),
        result,
        run.llm_client,
        spec.config,
        run.progress,
        repo_wiki_style=spec.wiki_style,
        vector_store=run.vector_store,
        prior_pages=prior_pages,
        session_factory=run.session_factory,
        repo_id=spec.repo_id,
    )


async def _persist_index_result(run: _JobRun, result: Any, incremental_pages: list) -> Any:
    """Write the pipeline result and any regenerated pages; return the swept page ids."""
    repo_id = run.spec.repo_id
    async with get_session(run.session_factory) as session:
        swept_page_ids = await persist_pipeline_result(result, session, repo_id)

        if incremental_pages:
            from repowise.core.persistence import upsert_pages_from_generated

            await upsert_pages_from_generated(session, incremental_pages, repo_id)

            # These pages land after persist_pipeline_result rebuilt the tree
            # and carry no placement, so rebuild again.
            from repowise.core.pipeline.page_tree_sync import rebuild_page_tree

            await rebuild_page_tree(session, repo_id)

        # Vector deletes before the SQL commit: a separate store, so no lock
        # conflict, and the idempotent delete keeps the durable commit last.
        if swept_page_ids and run.vector_store is not None:
            await run.vector_store.delete_many(swept_page_ids)
    return swept_page_ids


async def _refresh_fts(fts: Any, swept_page_ids: Any, pages: list) -> None:
    """FTS writes, after the session closes: the index can share its SQLite
    file, whose write lock the session would hold."""
    if fts is None:
        return
    if swept_page_ids:
        await fts.delete_many(swept_page_ids)
    for page in pages:
        await fts.index(
            page.page_id,
            page.title,
            page.content,
            summary=page.summary,
            target_path=page.target_path,
        )


async def _record_index_completion(
    run: _JobRun, result: Any, all_pages: list, elapsed: float
) -> int:
    """Mark the job completed with its run summary; return the pages generated."""
    from repowise.core.generation.models import count_stub_fallbacks

    pages_generated = len(all_pages)
    # Stubs from failed provider calls count as failed, or an outage reads
    # as a clean run.
    stub_fallbacks = count_stub_fallbacks(all_pages)
    async with get_session(run.session_factory) as session:
        await _complete_job_row(
            session,
            run.spec.job_id,
            run.spec.config,
            {
                "total_input_tokens": sum(p.input_tokens for p in all_pages),
                "total_output_tokens": sum(p.output_tokens for p in all_pages),
                "elapsed_seconds": round(elapsed, 1),
                "file_count": result.file_count,
                "symbol_count": result.symbol_count,
                "pages_generated": pages_generated,
            },
            completed_pages=(
                pages_generated - stub_fallbacks if pages_generated else result.file_count
            ),
            failed_pages=stub_fallbacks,
            total_pages=pages_generated if pages_generated else result.file_count,
        )
    return pages_generated


async def _write_index_state(run: _JobRun, docs: _DocsPlan, *, total_pages: int) -> None:
    """Update state.json so CLI incremental updates know the new baseline.

    An initial index also persists the full baseline (docs flags, run
    mode, config) that `repowise init` would have written.
    """
    spec = run.spec
    try:
        if spec.is_initial_index:
            await asyncio.to_thread(
                _persist_initial_index_state,
                Path(spec.repo_path),
                llm_client=run.llm_client,
                docs_mode=docs.docs_mode,
                docs_skip_reason=run.docs_skip_reason,
                total_pages=total_pages,
                wiki_style=spec.wiki_style,
                exclude_patterns=spec.exclude_patterns,
            )
        else:
            await asyncio.to_thread(_stamp_last_sync_commit, Path(spec.repo_path))
    except Exception:
        logger.debug("state_json_update_failed", job_id=spec.job_id, exc_info=True)


async def _reload_cross_repo_enricher(app_state: Any, job_id: str) -> None:
    """Hot-reload cross-repo enricher if available (workspace mode)."""
    try:
        enricher = getattr(app_state, "cross_repo_enricher", None)
        if enricher is not None and hasattr(enricher, "reload"):
            enricher.reload()
            # Workspace only. The contract map and the test-impact indexes
            # change together, so reloading one invalidates both.
            from repowise.server.mcp_server._test_impact import (
                close_test_impact_indexes,
            )

            await close_test_impact_indexes()
    except Exception:
        logger.debug("enricher_reload_failed", job_id=job_id, exc_info=True)
