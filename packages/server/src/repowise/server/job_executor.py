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


# Valid job execution modes handled by execute_job.
# NOTE: "generate" (HTTP repowise generate) and "single_page" (scoped page resync)
# are dispatched to _run_generate_job below, so validation must accept them upfront.
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
                    total_pages=self._total if self._total is not None else 0,
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
            # Keyless deterministic fallback: a first index of a repo with NO
            # provider still renders a complete template wiki (no model, no key, no
            # cost), exactly like `repowise init --index-only`, so the repo reads as
            # a real, upgradable wiki in the web UI rather than an empty index.
            # Scoped to initial_index: a keyless full_resync of a model-written wiki
            # must not overwrite every page with a template (and full_resync writes
            # no docs_mode here anyway).
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
        # With a provider the pages are model-written ("llm"); with none they
        # are rendered from templates ("deterministic"); only a run that asked
        # for no docs ends up with "none".
        return "llm" if self.llm else "deterministic" if self.deterministic else "none"


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
            # Resolve excludes while ``repo`` is still session-attached. Every
            # job entry point flows through here, so this covers them all.
            exclude_patterns=_repo_exclude_patterns(repo, repo_path),
            # Resolve the wiki style while ``repo`` is still session-attached.
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
    """Vector writes and deletes must follow the repository, just like its
    routed SQL session. This opens/creates <repo>/.repowise/lancedb and
    caches it by repo id; the global primary store is only a fallback for
    partially initialized development app states."""
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

        # Pass the repo id *and* path so the job resolves exactly what
        # the UI's provider picker chose. The picker persists its choice
        # per repo, under ``repos[repo_id]`` — the most specific step in
        # the resolver and the only one that carries a deliberate user
        # decision. Resolving on path alone skipped it entirely, so a
        # repo whose settings named a provider still fell through to the
        # auto-detect step and indexed with whatever it guessed.
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

    # ---- Mark completed ------------------------------------------------
    # Stop progress updates before writing final status to prevent a
    # late "running" update from overwriting "completed".
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

    Sync runs run_pipeline(generate_docs=False) for the full index,
    then regenerates only the wiki pages affected by recent changes.
    This keeps docs fresh without the cost of a full re-index.
    """
    spec = run.spec
    if spec.mode != "sync" or run.llm_client is None:
        return []
    # D3: hand the incremental regen the vector store (so re-rendered
    # pages are re-embedded, not silently dropped from semantic search)
    # and the prior pages (so an unchanged page whose prompt still hashes
    # the same is reused, not re-billed on every sync).
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

        # Persist incrementally regenerated pages (batched: one SELECT +
        # one flush instead of a round-trip per page on the hosted DB).
        if incremental_pages:
            from repowise.core.persistence import upsert_pages_from_generated

            await upsert_pages_from_generated(session, incremental_pages, repo_id)

            # These pages were generated from a changed-file subset, so
            # they carry the placement a partial set could work out, which
            # is none. They land after persist_pipeline_result already
            # rebuilt the tree, so rebuild again or they stay unplaced.
            from repowise.core.pipeline.page_tree_sync import rebuild_page_tree

            await rebuild_page_tree(session, repo_id)

        # Drop swept pages from the vector store *before* the SQL session
        # commits. The vector store is a separate engine/file (pgvector DB,
        # LanceDB dir, or in-memory), so there is no SQLite write-lock
        # conflict and the idempotent delete leaves the durable SQL commit
        # last: an interrupted run self-heals (embedding already gone, SQL
        # rows follow on commit).
        if swept_page_ids and run.vector_store is not None:
            await run.vector_store.delete_many(swept_page_ids)
    return swept_page_ids


async def _refresh_fts(fts: Any, swept_page_ids: Any, pages: list) -> None:
    """FTS deletes/indexing run after the session closes: the FTS index can
    share the SQLite file with the session, so writing it while the
    session holds a write lock raises "database is locked". The swept-id
    delete is idempotent (orphan FTS rows only) and must stay here."""
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
    # A stub the generator put up for a failed provider call has a row but
    # no prose. The hosted UI reads this job row, so counting it as
    # completed is what would let an outage look like a clean run.
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
            # Imported here because a single-repo server never gets this
            # far: the contract map and the consumer indexes the join
            # reads change together, so a reload of one invalidates both.
            from repowise.server.mcp_server._test_impact import (
                close_test_impact_indexes,
            )

            await close_test_impact_indexes()
    except Exception:
        logger.debug("enricher_reload_failed", job_id=job_id, exc_info=True)
