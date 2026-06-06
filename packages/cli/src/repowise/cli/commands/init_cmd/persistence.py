"""Database / state / config persistence for ``repowise init``.

Saves a :class:`PipelineResult` to the repo-local SQLite database, manages the
resume controller + ledger, and writes ``state.json`` / ``config.yaml`` for both
index-only and full (docs) runs. The single-repo and workspace flows both route
their persistence through here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repowise.cli._repo_session import open_repo_db
from repowise.cli.helpers import (
    config_fingerprint,
    get_head_commit,
    load_state,
    run_async,
    save_config,
    save_state,
)
from repowise.cli.state_persistence import build_kg_state, save_knowledge_graph_json


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


async def persist_result(result: Any, repo_path: Path) -> None:
    """Persist a PipelineResult to the local SQLite database.

    Handles both index-only (no pages) and full (with pages + FTS) modes.
    """
    from datetime import UTC, datetime

    from repowise.core.persistence import FullTextSearch, get_session, upsert_repository
    from repowise.core.persistence.crud import upsert_generation_job
    from repowise.core.pipeline import (
        persist_analysis,
        persist_generation,
        persist_pipeline_result,
        sweep_stale_generated_pages,
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
            # 1:1 with a layer); types that are neither stay protected.
            swept_page_ids = await sweep_stale_generated_pages(
                session,
                repo.id,
                result.generated_pages,
                getattr(result, "authoritative_page_types", None),
            )
        else:
            swept_page_ids = await persist_pipeline_result(result, session, repo.id)

        # Record a completed GenerationJob so the web UI can show
        # "last synced" / "last re-indexed" timestamps.
        now = datetime.now(UTC)
        page_count = len(result.generated_pages) if result.generated_pages else 0
        job = await upsert_generation_job(
            session,
            repository_id=repo.id,
            status="completed",
            total_pages=page_count,
            config={"mode": "full_resync", "source": "cli_init"},
        )
        job.completed_pages = page_count
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
    if fts is not None and swept_page_ids:
        await fts.delete_many(swept_page_ids)
    if fts is not None and result.generated_pages:
        for page in result.generated_pages:
            await fts.index(page.page_id, page.title, page.content)

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
    embedder_name_resolved: str,
    exclude_patterns: list[str],
    commit_limit: int | None,
    resolved_commit_limit: int,
    resolved_reasoning: str,
    include_submodules: bool = False,
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
    state["docs_enabled"] = True
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
    kg = getattr(result, "knowledge_graph_result", None)
    if kg is not None:
        state["knowledge_graph"] = build_kg_state(kg)
    save_state(repo_path, state)

    if kg is not None:
        save_knowledge_graph_json(repo_path, kg)

    save_config(
        repo_path,
        provider.provider_name,
        provider.model_name,
        embedder_name_resolved,
        exclude_patterns=exclude_patterns if exclude_patterns else None,
        commit_limit=resolved_commit_limit if commit_limit is not None else None,
        reasoning=resolved_reasoning,
    )

    # Re-save state with the fingerprint now that config.yaml is written.
    state["config_fingerprint"] = config_fingerprint(repo_path)
    save_state(repo_path, state)
