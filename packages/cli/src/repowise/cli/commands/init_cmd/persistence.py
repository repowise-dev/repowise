"""Database / state / config persistence for ``repowise init``.

Saves a :class:`PipelineResult` to the repo-local SQLite database, manages the
resume controller + ledger, and writes ``state.json`` / ``config.yaml`` for both
index-only and full (docs) runs. The single-repo and workspace flows both route
their persistence through here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from repowise.cli._repo_session import open_repo_db
from repowise.cli.helpers import (
    config_fingerprint,
    get_head_commit,
    head_commit_ts,
    load_config,
    load_state,
    run_async,
    save_config,
    save_state,
    stamp_offered_slots,
)
from repowise.cli.state_persistence import build_kg_state, save_knowledge_graph_json
from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION
from repowise.core.docs_mode import docs_mode_state_fields
from repowise.core.generation.models import count_stub_fallbacks

logger = structlog.get_logger(__name__)


async def build_resume_controller(repo_path: Path, *, resume: bool) -> tuple[Any, Any]:
    """Create the repo row + a ResumeController bound to a fresh engine.

    Returns ``(controller, engine)``. The caller runs the pipeline in the same
    event loop and disposes the engine afterwards. The repository row is
    created up front so the controller checkpoints against a real
    ``Repository.id`` (not ``str(repo_path)``), and so an interrupted run
    leaves a resumable, persisted index behind.
    """
    from repowise.core.pipeline.resume import ResumeController

    engine, sf, repo_id = await open_repo_db(repo_path)
    return ResumeController(sf, repo_id, resume=resume), engine


# Chunk size for the preserved-page FTS backfill — keeps the IN (...) below
# under SQLite's host-parameter limit and bounds how much page content is held
# in memory at once.
_FTS_BACKFILL_CHUNK = 200


async def _index_preserved_pages(sf: Any, fts: Any, preserved_page_ids: set[str] | None) -> None:
    """Full-text index the pages a resumed run kept rather than regenerated.

    ``page_fts`` is a standalone FTS5 table with no triggers: a row appears
    only when something calls :meth:`FullTextSearch.index`. The incremental
    flush during generation writes SQL and nothing else, so a run killed before
    its final persist leaves pages that exist in ``wiki_pages`` and are absent
    from search. Those are exactly the pages a resume preserves, which would
    make "your pages were kept" true in the database and false in every search
    that goes looking for them.

    Reads title + content back from SQL because a preserved page was never in
    this run's memory. ``index`` is delete-then-insert, so re-indexing a page
    that already had a row is a no-op in effect. Best-effort: search being
    stale must never fail a run whose pages are already committed.
    """
    if fts is None or not preserved_page_ids:
        return

    from sqlalchemy import select

    from repowise.core.persistence import get_session
    from repowise.core.persistence.models import Page

    ids = sorted(preserved_page_ids)
    try:
        for i in range(0, len(ids), _FTS_BACKFILL_CHUNK):
            batch = ids[i : i + _FTS_BACKFILL_CHUNK]
            async with get_session(sf) as session:
                rows = (
                    await session.execute(
                        select(
                            Page.id,
                            Page.title,
                            Page.content,
                            Page.summary,
                            Page.target_path,
                        ).where(Page.id.in_(batch))
                    )
                ).all()
            await fts.index_many(
                [
                    (page_id, title or "", content or "", summary or "", target_path or "")
                    for page_id, title, content, summary, target_path in rows
                ]
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("persist.preserved_fts_backfill_failed", error=str(exc))


async def persist_result(
    result: Any,
    repo_path: Path,
    progress: Any | None = None,
    timings: Any | None = None,
) -> None:
    """Persist a PipelineResult to the local SQLite database.

    Handles both index-only (no pages) and full (with pages + FTS) modes.

    *progress* is optional, and closing the phase is all it is used for here.
    The full-text index used to be written a page at a time, which was minutes
    of silence on a large wiki and earned a bar with a real denominator; it is
    one statement now, and nothing else persistence does is proportional to
    the page count, so the phase the CLI opens stays indeterminate.

    *timings* splits that span into its SQL and full-text halves, which the
    single ``persist`` number cannot distinguish.
    """
    from datetime import UTC, datetime

    from repowise.core.persistence import FullTextSearch, get_session, upsert_repository
    from repowise.core.persistence.crud import upsert_generation_job
    from repowise.core.pipeline import (
        persist_analysis,
        persist_generation,
        persist_pipeline_result,
        sweep_stale_generated_pages,
        timed,
        tombstone_absent_file_pages,
    )

    engine, sf, _repo_id = await open_repo_db(repo_path, repo_name=result.repo_name)

    # When a ResumeController persisted the INDEX phase incrementally during
    # the run, the graph + git + symbols are already on disk — write only the
    # analysis + generation outputs here (avoids re-persisting a rehydrated
    # graph and a redundant full index write).
    index_done = bool(getattr(result, "index_persisted_incrementally", False))

    fts = None
    if result.generated_pages:
        fts = FullTextSearch(engine)
        await fts.ensure_index()

    async with get_session(sf) as session:
        repo = await upsert_repository(
            session,
            name=result.repo_name,
            local_path=str(repo_path),
        )
        # Persist the detected tech stack into the repository's settings
        # blob. Merge into any pre-existing settings so we don't clobber
        # unrelated state (workspace flags, etc.). Done here rather than
        # in upsert_repository so the persistence helper stays
        # signature-stable.
        if getattr(result, "tech_stack", None):
            import json as _json

            try:
                existing = _json.loads(repo.settings_json or "{}")
                if not isinstance(existing, dict):
                    existing = {}
            except Exception:
                existing = {}
            existing["tech_stack"] = result.tech_stack
            repo.settings_json = _json.dumps(existing)
        swept_page_ids: list[str] = []
        if index_done:
            await persist_analysis(result, session, repo.id)
            await persist_generation(result, session, repo.id)
            # persist_generation has already upserted the current pages, so the
            # sweep only retires structurally-keyed pages this run did not
            # reproduce. Without it the incremental-index path (every normal
            # single-repo init) strands stale community-N / scc / layer pages
            # forever. A type is swept when the run produced pages of it OR
            # declared authority over it (curated runs are authoritative for
            # module/layer pages even when every module page was skipped as
            # 1:1 with a layer); types that are neither stay protected. Pages a
            # resumed run skipped count as reproduced — they are absent from
            # ``generated_pages`` precisely because they already exist.
            swept_page_ids = await sweep_stale_generated_pages(
                session,
                repo.id,
                result.generated_pages,
                getattr(result, "authoritative_page_types", None),
                getattr(result, "preserved_page_ids", None),
            )
        else:
            swept_page_ids = await persist_pipeline_result(result, session, repo.id)

        # Tombstone pages whose file is simply gone. The other tombstone path
        # reads a diff, and an init compares nothing, so until here a page
        # written before its file was deleted stayed ``fresh`` through every
        # later index. Outside the branch above because both branches need it.
        # Its ids join the swept ones so they leave the vector store and the
        # full-text index together, below, after this session closes.
        try:
            swept_page_ids = [
                *swept_page_ids,
                *await tombstone_absent_file_pages(session, repo.id, repo_path),
            ]
        except Exception as exc:  # one stale page must not fail a whole index
            logger.warning("tombstone_absent_sweep_failed", error=str(exc))

        # Record a completed GenerationJob so the web UI can show
        # "last synced" / "last re-indexed" timestamps.
        now = datetime.now(UTC)
        _pages = result.generated_pages or []
        page_count = len(_pages)
        # A page that fell back to its stub has a row, so it counts towards the
        # total, but the model never wrote it. Counting it as completed is what
        # would let a run that lost half its pages report a clean sweep to the
        # web UI, which reads this row rather than the job checkpoint.
        stub_fallbacks = count_stub_fallbacks(_pages)
        job = await upsert_generation_job(
            session,
            repository_id=repo.id,
            status="completed",
            total_pages=page_count,
            config={"mode": "full_resync", "source": "cli_init"},
        )
        job.completed_pages = page_count - stub_fallbacks
        job.failed_pages = stub_fallbacks
        job.started_at = now
        job.finished_at = now

        # Drop swept pages from the vector store *before* the SQL session
        # commits. The vector store is a separate engine/file (pgvector DB,
        # LanceDB dir, or in-memory) so there is no SQLite write-lock conflict,
        # and the delete is idempotent. Keeping the SQL commit last as the
        # durable source of truth means an interrupted run self-heals: the
        # vector embedding is already gone, the SQL rows follow on commit.
        if swept_page_ids:
            store = getattr(result, "vector_store", None)
            if store is not None:
                await store.delete_many(swept_page_ids)

    # FTS deletes/indexing run outside the session: the FTS index lives in the
    # *same* SQLite file as the session, so touching it while the session still
    # holds a write lock raises "database is locked". The swept-id delete must
    # therefore stay here (it cannot move ahead of the SQL commit like the
    # vector delete can); it is idempotent and narrow (orphan FTS rows only).
    with timed(timings, "persist.fts"):
        if fts is not None and swept_page_ids:
            await fts.delete_many(swept_page_ids)
        if fts is not None and result.generated_pages:
            await fts.index_many(
                [
                    (page.page_id, page.title, page.content, page.summary, page.target_path)
                    for page in result.generated_pages
                ]
            )
        await _index_preserved_pages(sf, fts, getattr(result, "preserved_page_ids", None))

    # Stamp the analysis (+ generation) phases in the resume ledger now that
    # they're persisted, so a future resume can skip them too.
    if index_done:
        from repowise.core.pipeline.resume import ResumeLedger, ResumePhase

        async with get_session(sf) as _ls:
            repo_id = (
                await upsert_repository(_ls, name=result.repo_name, local_path=str(repo_path))
            ).id
        ledger = ResumeLedger(sf, repo_id)
        await ledger.mark_completed(ResumePhase.ANALYSIS)
        if result.generated_pages:
            await ledger.mark_completed(ResumePhase.GENERATION)

    await engine.dispose()
    # Closed here rather than after the indexing loop: the preserved-page
    # backfill and the ledger stamp are persistence too, and a phase that
    # stopped short of them reported a different span per run mode.
    if progress is not None:
        progress.on_phase_done("persist")


# ---------------------------------------------------------------------------
# run-mode / git-tier resume helpers
# ---------------------------------------------------------------------------


def git_tier_for_run_mode(run_mode: str) -> str:
    """Map a CLI run-mode to the git index tier it persisted.

    Fast mode indexes the ESSENTIAL tier (no per-file blame / co-change);
    standard mode indexes FULL. Recorded in state.json so ``--resume`` and
    ``repowise update`` know which tier already exists on disk.
    """
    return "essential" if run_mode == "fast" else "full"


def effective_run_mode_for_resume(repo_path: Path, run_mode: str, resume: bool) -> str:
    """On ``--resume``, continue the git tier the prior run used.

    A fast (ESSENTIAL-tier) run resumed *without* re-passing ``--mode fast``
    would otherwise default to STANDARD and silently redo the expensive FULL
    git indexing the first run deliberately skipped (issue #341). We restore
    the persisted ``run_mode`` unless the user explicitly asked for fast on
    the resume invocation (in which case fast already wins).
    """
    if not resume or run_mode == "fast":
        return run_mode
    prev = load_state(repo_path).get("run_mode")
    return prev if prev in ("fast", "standard") else run_mode


# ---------------------------------------------------------------------------
# state.json + config.yaml writers
# ---------------------------------------------------------------------------


def save_full_state_and_config(
    *,
    repo_path: Path,
    result: Any,
    provider: Any,
    phase_timings: dict[str, float],
    degraded: list[str] | None = None,
    embedder_name_resolved: str,
    exclude_patterns: list[str],
    commit_limit: int | None,
    resolved_commit_limit: int,
    resolved_reasoning: str,
    include_submodules: bool = False,
    save_key: bool = True,
) -> None:
    """Persist state.json + config for a completed full-mode (docs) init run."""

    async def _count_db_pages() -> int:
        from sqlalchemy import func as sa_func
        from sqlalchemy import select as sa_select

        from repowise.cli.helpers import get_db_url_for_repo as _get_url
        from repowise.core.persistence import create_engine, create_session_factory, get_session
        from repowise.core.persistence.models import Page, Repository

        _engine = create_engine(_get_url(repo_path))
        _sf = create_session_factory(_engine)
        async with get_session(_sf) as _sess:
            repo_result = await _sess.execute(
                sa_select(Repository.id).where(Repository.local_path == str(repo_path))
            )
            _repo_id = repo_result.scalar_one_or_none()
            if _repo_id is None:
                await _engine.dispose()
                return len(result.generated_pages or [])

            count_result = await _sess.execute(
                sa_select(sa_func.count()).select_from(Page).where(Page.repository_id == _repo_id)
            )
            count = count_result.scalar_one()
        await _engine.dispose()
        return count

    head = get_head_commit(repo_path)
    state = load_state(repo_path)
    state["last_sync_commit"] = head
    state["total_pages"] = run_async(_count_db_pages())
    state["provider"] = provider.provider_name
    state["model"] = provider.model_name
    state.update(docs_mode_state_fields("llm"))
    # Full-mode docs runs always index the FULL git tier.
    state["run_mode"] = "standard"
    state["git_tier"] = "full"
    # Same pattern as git_tier: `repowise update` reads this back so its
    # graph rebuild keeps the init run's submodule boundary semantics.
    state["include_submodules"] = include_submodules
    total_tokens = sum(p.total_tokens for p in (result.generated_pages or []))
    state["total_tokens"] = total_tokens
    if phase_timings:
        state["phase_timings"] = phase_timings
    # What this run degraded on. A scripted run exits 0 either way, so without
    # this an agent records a clean index for a run that skipped, say, execution
    # flow tracing entirely.
    #
    # Cleared first, because ``state`` is the *previous* run's dict read back
    # from disk. Only setting it would let one bad run mark a repo degraded
    # forever: the re-run that fixed the problem writes no key, the stale list
    # is re-serialised verbatim, and every later ``update`` carries it on. The
    # key describes this run or it is absent.
    state.pop("degraded", None)
    if degraded:
        state["degraded"] = list(degraded)
    kg = getattr(result, "knowledge_graph_result", None)
    if kg is not None:
        state["knowledge_graph"] = build_kg_state(kg)
    # A full init/index genuinely brings the store to the current store format
    # (its pages are the concept tree), so stamp the terminal version rather
    # than clamping at the reindex gate a routine persist would stop below.
    #
    # The same run offered every registered onboarding slot its signals, so
    # record which ones. A slot absent from a fresh index was refused by its
    # gate, not missed, and the missing-slot notice must not report it.
    # Read back rather than passed in: init writes ``enable_onboarding: false``
    # to config.yaml before it reaches here, and only when it is false, so the
    # file is the run's own answer by the time this runs.
    stamp_offered_slots(state, enabled=bool(load_config(repo_path).get("enable_onboarding", True)))
    save_state(repo_path, state, full_index=True)

    if kg is not None:
        save_knowledge_graph_json(repo_path, kg)

    from repowise.cli.providers.embedders import resolve_embedding_model

    save_config(
        repo_path,
        provider.provider_name,
        provider.model_name,
        embedder_name_resolved,
        embedding_model=resolve_embedding_model(embedder_name_resolved),
        exclude_patterns=exclude_patterns if exclude_patterns else None,
        commit_limit=resolved_commit_limit if commit_limit is not None else None,
        reasoning=resolved_reasoning,
        save_key=save_key,
    )

    # Re-save state with the fingerprint now that config.yaml is written.
    state["config_fingerprint"] = config_fingerprint(repo_path)
    # This index's health rows were written by the current analyzer, so start
    # tracking it here — otherwise a fresh install carries no stamp and the
    # first analyzer change after it cannot tell it needs a re-score.
    state["health_analyzer_version"] = HEALTH_ANALYZER_VERSION
    # This run just scored every file, so the periodic re-score cadence starts
    # now. Without the stamp the gate reads "never re-scored" and the very next
    # update re-scores the whole repo that init had only just finished scoring.
    #
    # Only when there really is a report. The health phase swallows its own
    # failures and returns None, and nothing is persisted for a None report, so
    # stamping there would suppress the first update's re-score - the only thing
    # that would have repopulated the missing rows. Same rule the two
    # update-side writers follow: they stamp only on a re-score that returned
    # True. None (no git) is left unstamped too: the gate cannot fire without a
    # head_ts either, so there is nothing to suppress.
    _head_ts = head_commit_ts(repo_path) if getattr(result, "health_report", None) else None
    if _head_ts is not None:
        state["last_full_rescore_at"] = _head_ts
    save_state(repo_path, state, full_index=True)
