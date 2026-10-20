"""The docs-mode update writer: every DB step of a full update, in order.

The steps the index-only writer also runs are its own step functions from
:mod:`repowise.core.pipeline.incremental.persist_index`; the docs-only ones
(pages, stale decay, decisions, generation job, full-text indexing) live here.
:data:`FULL_UPDATE_STEPS` runs inside the session, :data:`FULL_UPDATE_CLEANUP`
after it closes.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from repowise.core.pipeline.incremental.persist_index import (
    IncrementalPersist,
    capture_commits,
    mark_tombstones,
    persist_doc_drift,
    persist_edges,
    persist_git,
    persist_health,
    persist_knowledge_graph,
    persist_security,
    persist_symbols,
    prune_deleted,
    rebuild_tree,
    reconcile_generation_scope,
    reconcile_scope,
    sweep_retired,
)
from repowise.core.pipeline.phase_timing import timed
from repowise.core.pipeline.step_runner import Step, run_steps

# The writer's degraded steps keep logging under the module that calls it.
log = structlog.get_logger("repowise.cli.commands.update_cmd.persistence")


@dataclass(kw_only=True)
class FullUpdatePersist(IncrementalPersist):
    """Inputs of one docs-mode write, on top of the index-only writer's."""

    repo_name: str = ""
    generated_pages: list = field(default_factory=list)
    new_decision_markers: list = field(default_factory=list)
    # The shared page/decision vector store, despite the name.
    decision_vector_store: Any | None = None
    provider: Any = None
    decay_paths: list[str] | None = None
    reconcile_full_generation: bool = False
    require_config_rebuild_success: bool = False
    require_decision_persist_success: bool = False
    degraded_before_persist: int = 0
    total_pages: int = 0

    def skip(self, step: str, exc: Exception, *, range_scoped: bool = False) -> None:
        log.warning("update_persist_step_degraded", step=step, error=str(exc))
        self.degraded.append(f"{step}: {exc}")
        if range_scoped and self.failed_steps is not None:
            self.failed_steps.append(step)


def _requires_rebuild(ctx: FullUpdatePersist) -> bool:
    return ctx.require_config_rebuild_success


async def _drop_vectors(ctx: FullUpdatePersist, page_ids: list[str]) -> None:
    if page_ids and ctx.decision_vector_store is not None:
        await ctx.decision_vector_store.delete_many(page_ids)


# --- Steps inside the session -------------------------------------------------


async def reconcile_scope_step(ctx: FullUpdatePersist) -> None:
    await reconcile_scope(ctx)
    await _drop_vectors(ctx, ctx.tombstoned_page_ids)


async def upsert_pages(ctx: FullUpdatePersist) -> None:
    # Batched (one SELECT + one flush); the checkpointer sink already streamed
    # each page per-commit for durability.
    from repowise.core.persistence import upsert_pages_from_generated

    with timed(ctx.timings, "persist.pages"):
        await upsert_pages_from_generated(ctx.session, ctx.generated_pages, ctx.repo_id)


async def reconcile_generation_step(ctx: FullUpdatePersist) -> None:
    await reconcile_generation_scope(ctx)
    await _drop_vectors(ctx, ctx.tombstoned_page_ids)


async def sweep_retired_step(ctx: FullUpdatePersist) -> None:
    await sweep_retired(ctx)
    # Before the SQL session commits, as ``init`` does: a separate engine and
    # an idempotent delete, so an interrupted run self-heals forwards.
    await _drop_vectors(ctx, ctx.swept_page_ids)


async def backfill_related(ctx: FullUpdatePersist) -> None:
    # The generation pass only touched this run's pages; this LLM-free
    # backfill heals every other page in the same transaction.
    from repowise.core.generation.related_pages import file_import_edges
    from repowise.core.persistence.crud import backfill_related_pages

    with timed(ctx.timings, "persist.related_pages"):
        await backfill_related_pages(
            ctx.session,
            ctx.repo_id,
            import_edges=file_import_edges(ctx.graph_builder),
            git_meta_map=ctx.git_meta_map,
            pagerank=ctx.graph_builder.pagerank(),
            # This run's pages carry fresher metadata (including module
            # siblings) than the recompute could produce.
            skip_page_ids={p.page_id for p in ctx.generated_pages},
        )


async def decay_stale_pages(ctx: FullUpdatePersist) -> None:
    # Weakly-affected pages decay to 'stale' so coverage and get_stale_pages see them.
    from repowise.core.generation import GenerationConfig
    from repowise.core.generation.cascade import expand_cascade
    from repowise.core.generation.models import compute_page_id
    from repowise.core.generation.scope import build_dependencies, load_page_records
    from repowise.core.pipeline.persist import mark_page_ids_stale
    from repowise.core.pipeline.scoped_generation import _load_page_rows, load_kg_context
    from repowise.core.repo_config import load_repo_config

    with timed(ctx.timings, "persist.stale_pages"):
        if not ctx.decay_paths:
            return
        generation_config = GenerationConfig.from_repo_config(load_repo_config(ctx.repo_path))
        records = load_page_records(await _load_page_rows(ctx.session, ctx.repo_id))
        deps = build_dependencies(
            parsed_files=ctx.parsed_files or [],
            graph_builder=ctx.graph_builder,
            config=generation_config,
            kg_ctx=load_kg_context(Path(ctx.repo_path)),
            records=records,
            repo_name=ctx.repo_name,
        )
        seed_ids = {compute_page_id("file_page", path) for path in ctx.decay_paths}
        # mode="none": mark dependents stale, never regenerate them; that spend
        # is opt-in through `generate --stale`.
        cascade = expand_cascade(seed_ids, "none", deps)
        # A page this run refreshed is fresh; never decay it again afterwards.
        generated_ids = {page.page_id for page in ctx.generated_pages}
        await mark_page_ids_stale(
            ctx.session, ctx.repo_id, (cascade.stale_ids | seed_ids) - generated_ids
        )


async def repair_decisions(ctx: FullUpdatePersist) -> None:
    # The store repairs the full-index path runs, each failure its own
    # degraded step; a required persist fails the update on the first one.
    from repowise.core.pipeline.persist import repair_decision_store

    def skip(step: str, exc: Exception) -> None:
        ctx.skip(step, exc)
        if ctx.require_decision_persist_success:
            raise exc

    await repair_decision_store(
        ctx.session, ctx.repo_id, vector_store=ctx.decision_vector_store, skip=skip
    )


async def upsert_decisions(ctx: FullUpdatePersist) -> None:
    from repowise.core.persistence.crud import (
        bulk_upsert_decisions,
        purge_proposed_decisions_by_source,
        recompute_decision_staleness,
    )

    session, repo_id = ctx.session, ctx.repo_id
    if ctx.require_decision_persist_success:
        await purge_proposed_decisions_by_source(session, repo_id, "git_archaeology")
    decision_dicts = [dataclasses.asdict(d) for d in ctx.new_decision_markers or []]
    if decision_dicts:
        touched_ids = await bulk_upsert_decisions(
            session, repo_id, decision_dicts, vector_store=ctx.decision_vector_store
        )
        if touched_ids and ctx.decision_vector_store is not None:
            from repowise.core.analysis.decision_evolution import (
                detect_supersessions_and_conflicts,
            )

            await detect_supersessions_and_conflicts(
                session,
                repo_id,
                touched_ids=touched_ids,
                vector_store=ctx.decision_vector_store,
                provider=ctx.provider,
            )
    if ctx.git_meta_map:
        await recompute_decision_staleness(session, repo_id, ctx.git_meta_map)


DECISION_STEPS: tuple[Step, ...] = (
    # Reports its own failures, one repair at a time.
    Step("Decision store repairs", repair_decisions, guarded=False),
    Step(
        "Decision persist",
        upsert_decisions,
        reraise=lambda ctx: ctx.require_decision_persist_success,
    ),
)


async def persist_decisions(ctx: FullUpdatePersist) -> None:
    with timed(ctx.timings, "persist.decisions"):
        await run_steps(ctx, DECISION_STEPS)


async def apply_lifecycle(ctx: FullUpdatePersist) -> None:
    # Every run, not only when this one added records: the revert usually
    # lands after the decision it retires was stored.
    from repowise.core.pipeline.persist import apply_decision_lifecycle

    await apply_decision_lifecycle(ctx.session, ctx.repo_id, ctx.repo_path, skip=ctx.skip)


async def refresh_governance(ctx: FullUpdatePersist) -> None:
    from repowise.core.pipeline.persist import refresh_governance_findings

    with timed(ctx.timings, "persist.governance"):
        # The health step below finalizes the refactoring queue.
        await refresh_governance_findings(ctx.session, ctx.repo_id, recompose_queue=False)


async def persist_nodes(ctx: FullUpdatePersist) -> None:
    # Before health, whose plan annotations read each symbol's ``public_api``.
    from repowise.core.pipeline.persist import persist_graph_nodes

    with timed(ctx.timings, "persist.graph_nodes"):
        await persist_graph_nodes(ctx.session, ctx.repo_id, ctx.graph_builder)


async def persist_dead_code(ctx: FullUpdatePersist) -> None:
    # Repo-wide: dead code is a cross-file property, so a file-scoped write
    # would keep an unchanged module's stale verdict.
    from repowise.core.persistence.crud import replace_dead_code_findings

    with timed(ctx.timings, "persist.dead_code"):
        await replace_dead_code_findings(
            ctx.session,
            ctx.repo_id,
            [dataclasses.asdict(f) for f in ctx.dead_code_report.findings],
            scope=ctx.dead_code_report.authoritative_paths,
        )


async def refresh_externals(ctx: FullUpdatePersist) -> None:
    from repowise.core.pipeline.incremental import refresh_external_systems

    with timed(ctx.timings, "persist.external_systems"):
        await refresh_external_systems(ctx.session, ctx.repo_id, ctx.repo_path, ctx.file_diffs)


async def record_generation_job(ctx: FullUpdatePersist) -> None:
    # Updates the web UI "last synced" timestamp.
    from datetime import UTC, datetime

    from repowise.core.generation.models import count_stub_fallbacks
    from repowise.core.persistence.crud import upsert_generation_job

    now = datetime.now(UTC)
    page_count = len(ctx.generated_pages)
    # A stub the provider failure put up has a row but no prose, so it is
    # not a completed page.
    stub_fallbacks = count_stub_fallbacks(ctx.generated_pages)
    job = await upsert_generation_job(
        ctx.session,
        repository_id=ctx.repo_id,
        status="completed",
        total_pages=page_count,
        config={"mode": "incremental", "source": "cli_update"},
    )
    job.completed_pages = page_count - stub_fallbacks
    job.failed_pages = stub_fallbacks
    job.started_at = now
    job.finished_at = now


async def count_pages(ctx: FullUpdatePersist) -> None:
    # The real total for state.json; the upsert count stays if this degrades.
    from sqlalchemy import func, select

    from repowise.core.persistence.models import Page

    result = await ctx.session.execute(
        select(func.count()).select_from(Page).where(Page.repository_id == ctx.repo_id)
    )
    ctx.total_pages = int(result.scalar_one())


async def check_config_rebuild(ctx: FullUpdatePersist) -> None:
    if ctx.require_config_rebuild_success and len(ctx.degraded) > ctx.degraded_before_persist:
        raise RuntimeError(
            "Configuration rebuild did not update every dependent store; "
            "the previous fingerprint was retained so the next update retries."
        )


def _has_git_input(ctx: FullUpdatePersist) -> bool:
    return bool(ctx.git_meta_map or ctx.git_decay_map or ctx.full_git_summary is not None)


# Pages first and without a net: everything else is derived metadata, but a
# docs-mode update that cannot write pages failed. Range-scoped as in the
# index-only writer.
FULL_UPDATE_STEPS: tuple[Step, ...] = (
    Step(
        "Config scope reconciliation",
        reconcile_scope_step,
        when=lambda ctx: ctx.reconcile_full_scope,
        reraise=_requires_rebuild,
    ),
    Step("Page upsert", upsert_pages, guarded=False),
    Step(
        "Generation scope reconciliation",
        reconcile_generation_step,
        when=lambda ctx: ctx.reconcile_full_generation,
        reraise=_requires_rebuild,
    ),
    Step("Retired page sweep", sweep_retired_step),
    Step("Tombstone marking", mark_tombstones, range_scoped=True),
    Step("Related-pages backfill", backfill_related),
    Step("Stale-page decay", decay_stale_pages),
    Step("Page tree rebuild", rebuild_tree),
    Step(
        "Knowledge-graph persist",
        persist_knowledge_graph,
        when=lambda ctx: ctx.knowledge_graph_result is not None,
    ),
    # A full walk stamps the history version once persisted, so losing its
    # rows here must fail the run, not stamp it.
    Step(
        "Git persist",
        persist_git,
        when=_has_git_input,
        range_scoped=True,
        reraise=lambda ctx: ctx.require_config_rebuild_success
        or ctx.full_git_summary is not None,
    ),
    Step("Commit capture", capture_commits, when=_has_git_input),
    Step("Decisions", persist_decisions, guarded=False),
    Step("Decision lifecycle", apply_lifecycle, guarded=False),
    Step("Governance findings", refresh_governance),
    # Before health: the snapshot it takes records the stored drift count.
    Step(
        "Doc-drift persist",
        persist_doc_drift,
        when=lambda ctx: ctx.doc_drift_report is not None,
    ),
    Step("Graph nodes persist", persist_nodes),
    Step(
        "Health persist",
        persist_health,
        when=lambda ctx: ctx.partial_health_report is not None,
        range_scoped=True,
    ),
    Step(
        "Dead-code persist",
        persist_dead_code,
        when=lambda ctx: ctx.dead_code_report is not None,
        range_scoped=True,
    ),
    Step("Symbol persist", persist_symbols, range_scoped=True),
    Step("Security persist", persist_security, range_scoped=True),
    Step("Graph edges persist", persist_edges, range_scoped=True),
    Step("External systems refresh", refresh_externals, range_scoped=True),
    # Last of the store writes: the steps above upsert rows for deleted paths
    # too. Reports its own failures, one sub-step at a time.
    Step("Deleted-file prune", prune_deleted, guarded=False),
    Step("Generation job record", record_generation_job),
    Step("Page count", count_pages),
    Step("Config rebuild check", check_config_rebuild, guarded=False),
)


# --- Steps after the session closes -------------------------------------------


async def index_full_text(ctx: FullUpdatePersist) -> None:
    # Outside the transaction: rebuildable, and its writer manages its own
    # connection state.
    from repowise.core.persistence import FullTextSearch
    from repowise.core.pipeline.cleanup_debt import clear_cleanup_debt

    with timed(ctx.timings, "persist.fts"):
        fts = FullTextSearch(ctx.engine)
        await fts.ensure_index()
        await fts.index_pages(ctx.generated_pages)
        if ctx.fts_cleanup_ids:
            await fts.delete_many(sorted(ctx.fts_cleanup_ids))
        clear_cleanup_debt(Path(ctx.repo_path), "fts", ctx.fts_cleanup_ids)


async def delete_vectors(ctx: FullUpdatePersist) -> None:
    from repowise.core.pipeline.cleanup_debt import clear_cleanup_debt

    await ctx.decision_vector_store.delete_many(sorted(ctx.vector_cleanup_ids))
    clear_cleanup_debt(Path(ctx.repo_path), "vectors", ctx.vector_cleanup_ids)


def _record_fts_debt(ctx: FullUpdatePersist) -> None:
    from repowise.core.pipeline.cleanup_debt import record_cleanup_debt

    record_cleanup_debt(Path(ctx.repo_path), "fts", ctx.fts_cleanup_ids)


def _record_vector_debt(ctx: FullUpdatePersist) -> None:
    from repowise.core.pipeline.cleanup_debt import record_cleanup_debt

    record_cleanup_debt(Path(ctx.repo_path), "vectors", ctx.vector_cleanup_ids)


FULL_UPDATE_CLEANUP: tuple[Step, ...] = (
    Step(
        "Full-text search indexing",
        index_full_text,
        range_scoped=True,
        on_error=_record_fts_debt,
        reraise=_requires_rebuild,
    ),
    Step(
        "Deferred vector cleanup",
        delete_vectors,
        when=lambda ctx: bool(ctx.vector_cleanup_ids)
        and ctx.decision_vector_store is not None,
        on_error=_record_vector_debt,
        reraise=_requires_rebuild,
    ),
)


async def write_full_update(ctx: FullUpdatePersist) -> int:
    """Open the store, run every step in one transaction, then the cleanup tail."""
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        refresh_planner_stats,
        upsert_repository,
    )
    from repowise.core.pipeline.cleanup_debt import load_cleanup_debt

    timings = ctx.timings
    ctx.engine = create_engine(get_db_url_for_repo(ctx.repo_path))
    ctx.degraded_before_persist = len(ctx.degraded)
    ctx.total_pages = len(ctx.generated_pages)
    try:
        with timed(timings, "persist.open"):
            await init_db(ctx.engine)
            sf = create_session_factory(ctx.engine)

        async with get_session(sf) as session:
            with timed(timings, "persist.open"):
                repo = await upsert_repository(
                    session, name=ctx.repo_name, local_path=str(ctx.repo_path), stamp_head=True
                )
            ctx.session, ctx.repo_id = session, repo.id
            await run_steps(ctx, FULL_UPDATE_STEPS)
            # The transaction commits as the block exits.
            if timings is not None:
                timings.start("persist.commit")
        if timings is not None:
            timings.stop("persist.commit")

        debt = load_cleanup_debt(Path(ctx.repo_path))
        ctx.fts_cleanup_ids = set(ctx.tombstoned_page_ids) | set(ctx.swept_page_ids) | debt["fts"]
        # The union closes the mark_tombstone_pages path: the scope steps
        # already drop their ids, but a deleted file's embedding did not.
        ctx.vector_cleanup_ids = set(ctx.tombstoned_page_ids) | debt["vectors"]
        await run_steps(ctx, FULL_UPDATE_CLEANUP)

        # After the transaction, so the scan never holds the write lock, and on
        # every update, so a backlog an earlier budget left drains.
        from repowise.core.pipeline.commit_health import refresh_commit_health_after_update

        with timed(timings, "persist.commit_health"):
            await refresh_commit_health_after_update(sf, ctx.repo_id, str(ctx.repo_path))
        with timed(timings, "persist.planner_stats"):
            await refresh_planner_stats(ctx.engine)
        return ctx.total_pages
    finally:
        await ctx.engine.dispose()


__all__ = [
    "DECISION_STEPS",
    "FULL_UPDATE_CLEANUP",
    "FULL_UPDATE_STEPS",
    "FullUpdatePersist",
]
