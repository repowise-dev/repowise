"""The incremental index writer: every DB step of an update, in order.

Each step is a small function of the run's :class:`IncrementalPersist`, listed
once in :data:`INCREMENTAL_STEPS` (inside the session) and
:data:`CLEANUP_STEPS` (after it closes), and run by
:func:`~repowise.core.pipeline.step_runner.run_steps`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repowise.core.pipeline.phase_timing import PhaseTimings, timed
from repowise.core.pipeline.step_runner import PersistRun, Step, run_steps

from ._common import LogFn, _noop_log
from .commits import persist_incremental_commits
from .external_systems import refresh_external_systems
from .health import persist_partial_health
from .prune import prune_deleted_files


@dataclass(kw_only=True)
class IncrementalPersist(PersistRun):
    """Inputs of one incremental write, plus the state its steps pass along."""

    repo_path: Any
    graph_builder: Any
    git_meta_map: dict
    dead_code_report: Any = None
    partial_health_report: Any = None
    changed_paths: list[str] = field(default_factory=list)
    doc_drift_report: Any | None = None
    current_graph_file_paths: set[str] | None = None
    file_diffs: list[Any] | None = None
    knowledge_graph_result: Any | None = None
    parsed_files: list[Any] | None = None
    source_map: dict[str, Any] | None = None
    git_decay_map: dict | None = None
    full_git_summary: Any | None = None
    history_window_changed: bool = False
    reconcile_full_scope: bool = False
    full_generation_page_ids: set[str] | None = None
    vector_store: Any | None = None

    session: Any = None
    repo_id: str = ""
    engine: Any = None
    # Filled by the tombstone steps; read after the session closes, so they
    # have to survive a step that was skipped.
    tombstoned_page_ids: list[str] = field(default_factory=list)
    # Same contract, for rows of a page that has been retired outright.
    swept_page_ids: list[str] = field(default_factory=list)
    deselected_page_ids: list[str] = field(default_factory=list)
    fts_cleanup_ids: set[str] = field(default_factory=set)
    vector_cleanup_ids: set[str] = field(default_factory=set)


# --- Steps inside the session -------------------------------------------------


async def reconcile_scope(ctx: IncrementalPersist) -> None:
    from repowise.core.pipeline.persist import (
        reconcile_full_index_scope,
    )

    current_graph_paths = set(
        getattr(
            ctx.graph_builder,
            "traversed_file_paths",
            {pf.file_info.path for pf in ctx.parsed_files or []},
        )
    )
    current_git_paths = set(ctx.git_meta_map)
    with timed(ctx.timings, "persist.scope_reconcile"):
        ctx.tombstoned_page_ids = await reconcile_full_index_scope(
            ctx.session,
            ctx.repo_id,
            current_graph_paths,
            current_git_paths,
        )


async def reconcile_generation_scope(ctx: IncrementalPersist) -> None:
    from repowise.core.pipeline.persist import (
        tombstone_pages_outside_generation,
    )

    ctx.tombstoned_page_ids += await tombstone_pages_outside_generation(
        ctx.session, ctx.repo_id, ctx.full_generation_page_ids
    )


async def sweep_retired(ctx: IncrementalPersist) -> None:
    # Delete rows of pages retired since this index was built. This path never
    # regenerates a repo-wide page, so nothing else here would ever visit one
    # to notice it should be gone, and for a user whose updates all come from
    # the post-commit hook this is the only place a retirement can land.
    from repowise.core.pipeline.persist import (
        sweep_absent_cycle_pages,
        sweep_absent_symbol_spotlights,
        sweep_deselected_pages,
        sweep_retired_pages,
    )

    with timed(ctx.timings, "persist.sweeps"):
        ctx.swept_page_ids = await sweep_retired_pages(ctx.session, ctx.repo_id)
        # Same reasoning as the retirement sweep above: this path never
        # regenerates a cycle page, so asking the rebuilt graph whether the
        # cycle still exists is the only way a fixed cycle's page can ever be
        # retired for a user who only runs `update`. Spotlights of removed
        # symbols too.
        ctx.swept_page_ids += await sweep_absent_cycle_pages(
            ctx.session, ctx.repo_id, ctx.graph_builder
        )
        ctx.swept_page_ids += await sweep_absent_symbol_spotlights(
            ctx.session, ctx.repo_id, ctx.graph_builder
        )
        # Unlike the pages above, these may carry embeddings.
        ctx.deselected_page_ids = await sweep_deselected_pages(
            ctx.session,
            ctx.repo_id,
            ctx.parsed_files,
            ctx.graph_builder,
            _generation_config(ctx.repo_path),
            ctx.git_meta_map,
        )
        ctx.swept_page_ids += ctx.deselected_page_ids


def _generation_config(repo_path: Any) -> Any:
    from repowise.core.generation import GenerationConfig
    from repowise.core.repo_config import load_repo_config

    return GenerationConfig.from_repo_config(load_repo_config(Path(repo_path)))


async def mark_tombstones(ctx: IncrementalPersist) -> None:
    # Tombstone pages for deleted/renamed files FIRST — a fresh page for a
    # file that no longer exists misleads every retrieval consumer until the
    # next full regeneration.
    from repowise.core.pipeline.persist import (
        mark_tombstone_pages,
        tombstone_candidates,
    )

    with timed(ctx.timings, "persist.tombstones"):
        ctx.tombstoned_page_ids += await mark_tombstone_pages(
            ctx.session, ctx.repo_id, tombstone_candidates(ctx.file_diffs)
        )


async def rebuild_tree(ctx: IncrementalPersist) -> None:
    # Placement depends on the whole page set, which on an incremental run
    # lives in the store rather than in the pages just generated.
    from repowise.core.pipeline.page_tree_sync import rebuild_page_tree

    with timed(ctx.timings, "persist.page_tree"):
        await rebuild_page_tree(ctx.session, ctx.repo_id)


async def persist_git(ctx: IncrementalPersist) -> None:
    with timed(ctx.timings, "persist.git"):
        from repowise.core.pipeline.persist import persist_git_refresh

        await persist_git_refresh(
            ctx.session,
            ctx.repo_id,
            ctx.git_meta_map,
            ctx.git_decay_map,
            ctx.full_git_summary,
            keep_commit_health=not ctx.history_window_changed,
        )


async def capture_commits(ctx: IncrementalPersist) -> None:
    with timed(ctx.timings, "persist.commits"):
        if ctx.full_git_summary is None:
            await persist_incremental_commits(
                ctx.session, ctx.repo_id, ctx.repo_path, timings=ctx.timings
            )


async def persist_dead_code(ctx: IncrementalPersist) -> None:
    from repowise.core.persistence.crud import (
        replace_dead_code_findings,
    )

    # The report is repo-wide, and dead code is a cross-file property, so a
    # change-scoped write would drop every verdict the change flipped outside
    # the change set. The scope is the set of files whose confidence was
    # scored on real git metadata; the rest keep what they had.
    with timed(ctx.timings, "persist.dead_code"):
        await replace_dead_code_findings(
            ctx.session,
            ctx.repo_id,
            ctx.dead_code_report.findings,
            scope=ctx.dead_code_report.authoritative_paths,
        )


async def persist_doc_drift(ctx: IncrementalPersist) -> None:
    from repowise.core.persistence.crud import (
        replace_doc_drift_guarded,
    )

    with timed(ctx.timings, "persist.doc_drift"):
        await replace_doc_drift_guarded(ctx.session, ctx.repo_id, ctx.doc_drift_report)


async def persist_nodes(ctx: IncrementalPersist) -> None:
    # Re-persist graph_nodes so symbol-level PageRank / betweenness /
    # community ids stay in sync with the current graph build. Without this,
    # ``repowise update`` leaves stale per-symbol metrics from the original
    # init and the UI shows "Not indexed in graph" for every symbol on
    # existing repos. Before health, whose plan annotations read each
    # symbol's ``public_api`` from here; a failed write leaves them reading
    # the last run's flags.
    from repowise.core.pipeline.persist import persist_graph_nodes

    with timed(ctx.timings, "persist.graph_nodes"):
        await persist_graph_nodes(ctx.session, ctx.repo_id, ctx.graph_builder, timings=ctx.timings)


async def persist_health(ctx: IncrementalPersist) -> None:
    with timed(ctx.timings, "persist.health"):
        await persist_partial_health(
            ctx.session, ctx.repo_id, ctx.partial_health_report, ctx.repo_path
        )


async def persist_symbols(ctx: IncrementalPersist) -> None:
    # Refresh wiki_symbols for the changed files. Historically the incremental
    # path re-parsed but never persisted symbols, so their start/end bounds
    # fossilized at the last full index and the get_answer hydrator served
    # drifted signatures. Scoped to the changed set for cost.
    from repowise.core.pipeline.persist import persist_incremental_symbols

    with timed(ctx.timings, "persist.symbols"):
        await persist_incremental_symbols(
            ctx.session, ctx.repo_id, ctx.parsed_files, ctx.changed_paths
        )


async def persist_security(ctx: IncrementalPersist) -> None:
    from repowise.core.pipeline.persist import persist_incremental_security_findings

    with timed(ctx.timings, "persist.security"):
        await persist_incremental_security_findings(
            ctx.session, ctx.repo_id, ctx.parsed_files, ctx.source_map, ctx.changed_paths
        )


async def persist_edges(ctx: IncrementalPersist) -> None:
    # Refresh graph_edges for the changed files. The full-init path was
    # historically the only writer of edges, so adjacency froze at the last
    # full index: new imports/calls stayed invisible and dropped ones lingered
    # as false paths. Phase E flow-path traversal reads adjacency straight
    # from this table, so it decayed on every update.
    from repowise.core.pipeline.persist import persist_incremental_edges

    with timed(ctx.timings, "persist.edges"):
        await persist_incremental_edges(
            ctx.session, ctx.repo_id, ctx.graph_builder, ctx.parsed_files, ctx.changed_paths
        )


async def backfill_related(ctx: IncrementalPersist) -> None:
    # Refresh related-pages metadata across the whole wiki. LLM-free, so even
    # index-only updates heal pages generated before the feature shipped (or
    # drifted by new imports) in one run.
    from repowise.core.generation.related_pages import file_import_edges
    from repowise.core.persistence.crud import backfill_related_pages

    with timed(ctx.timings, "persist.related_pages"):
        changed_rel = await backfill_related_pages(
            ctx.session,
            ctx.repo_id,
            import_edges=file_import_edges(ctx.graph_builder),
            git_meta_map=ctx.git_meta_map,
            pagerank=ctx.graph_builder.pagerank(),
        )
    if changed_rel:
        ctx.log(f"Related pages refreshed on {changed_rel} pages")


async def persist_knowledge_graph(ctx: IncrementalPersist) -> None:
    from repowise.core.pipeline.persist import persist_kg

    with timed(ctx.timings, "persist.kg"):
        await persist_kg(ctx.knowledge_graph_result, ctx.session, ctx.repo_id)


async def refresh_externals(ctx: IncrementalPersist) -> None:
    # Refresh external systems (C4 L1) when a dependency manifest changed —
    # otherwise the architecture panel serves the init-time dep list forever.
    # Gated + no LLM (see refresh_external_systems).
    with timed(ctx.timings, "persist.external_systems"):
        await refresh_external_systems(
            ctx.session, ctx.repo_id, ctx.repo_path, ctx.file_diffs, log=ctx.log
        )


async def purge_retired_decisions(ctx: IncrementalPersist) -> None:
    # One-shot drain of proposals from the removed code_comment harvest
    # (#751). Runs on the index-only path too, because the post-commit hook's
    # updates never reach the full decision persist. Confirmed/dismissed rows
    # are kept.
    from repowise.core.persistence.crud import (
        purge_proposed_decisions_by_source,
    )

    await purge_proposed_decisions_by_source(ctx.session, ctx.repo_id, "code_comment")
    # A history-window change re-mines these; a walk that only refreshes rows
    # under new rules does not, so it keeps them.
    if ctx.full_git_summary is not None and ctx.history_window_changed:
        await purge_proposed_decisions_by_source(ctx.session, ctx.repo_id, "git_archaeology")


async def prune_deleted(ctx: IncrementalPersist) -> None:
    # Last in the session on purpose: everything above upserts, and the git
    # step indexes the changed *paths*, deleted ones included, so the prune
    # has to have the final say on what the store claims.
    await prune_deleted_files(
        ctx.session,
        ctx.repo_id,
        ctx.repo_path,
        ctx.graph_builder,
        (
            ctx.current_graph_file_paths
            if ctx.current_graph_file_paths is not None
            else {pf.file_info.path for pf in ctx.parsed_files or []}
        ),
        health_report=ctx.partial_health_report,
        skip=ctx.skip,
        log=ctx.log,
        degraded=ctx.degraded,
        timings=ctx.timings,
    )


def _has_git_input(ctx: IncrementalPersist) -> bool:
    return bool(ctx.git_meta_map or ctx.git_decay_map or ctx.full_git_summary is not None)


# Range-scoped steps are the ones a later run cannot heal on its own; the
# membership test is in persist_incremental_index's docstring.
INCREMENTAL_STEPS: tuple[Step, ...] = (
    Step(
        "Config scope reconciliation",
        reconcile_scope,
        when=lambda ctx: ctx.reconcile_full_scope,
    ),
    Step(
        "Generation scope reconciliation",
        reconcile_generation_scope,
        when=lambda ctx: ctx.full_generation_page_ids is not None,
    ),
    Step("Retired page sweep", sweep_retired),
    Step(
        "Tombstone marking",
        mark_tombstones,
        when=lambda ctx: bool(ctx.file_diffs),
        range_scoped=True,
    ),
    Step("Page tree rebuild", rebuild_tree),
    Step("Git persist", persist_git, when=_has_git_input, range_scoped=True),
    Step("Commit capture", capture_commits, when=_has_git_input),
    Step(
        "Dead-code persist",
        persist_dead_code,
        when=lambda ctx: ctx.dead_code_report is not None,
        range_scoped=True,
    ),
    # Not range_scoped: the pass re-derives every document each run, so a
    # skipped write heals itself on the next update.
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
    Step("Symbol persist", persist_symbols, range_scoped=True),
    Step("Security persist", persist_security, range_scoped=True),
    Step("Graph edges persist", persist_edges, range_scoped=True),
    Step("Related-pages backfill", backfill_related),
    Step(
        "Knowledge-graph persist",
        persist_knowledge_graph,
        when=lambda ctx: ctx.knowledge_graph_result is not None,
    ),
    Step(
        "External systems refresh",
        refresh_externals,
        when=lambda ctx: bool(ctx.file_diffs),
        range_scoped=True,
    ),
    Step("Decision purge", purge_retired_decisions),
    # Reports its own failures, one sub-step at a time.
    Step("Deleted-file prune", prune_deleted, guarded=False),
)


# --- Steps after the session closes -------------------------------------------
#
# On SQLite the full-text index shares the database file, so writing to it
# while the session holds a write lock raises "database is locked".
#
# A tombstone can never be an answer — hydration drops it — but retrieval
# fetches a fixed number of rows before that check runs, so every tombstone
# left in the index costs a real candidate its slot.
#
# A swept page's FTS row is worse than a tombstone: search hydrates title and
# snippet from the FTS copy itself, so an orphan answers in full while the
# page it names 404s.


async def delete_fts_rows(ctx: IncrementalPersist) -> None:
    from repowise.core.persistence.search import FullTextSearch
    from repowise.core.pipeline.cleanup_debt import clear_cleanup_debt

    fts = FullTextSearch(ctx.engine)
    with timed(ctx.timings, "persist.fts"):
        await fts.ensure_index()
        await fts.delete_many(sorted(ctx.fts_cleanup_ids))
    clear_cleanup_debt(Path(ctx.repo_path), "fts", ctx.fts_cleanup_ids)


async def open_vector_store(ctx: IncrementalPersist) -> None:
    from repowise.core.persistence.vector_store import LanceDBVectorStore
    from repowise.core.providers.embedding.base import MockEmbedder

    # Delete-only access never embeds or checks dimensions.
    ctx.vector_store = LanceDBVectorStore(str(_lance_dir(ctx)), embedder=MockEmbedder())


async def clear_vector_debt_without_store(ctx: IncrementalPersist) -> None:
    # No store means there are no vector rows left to clean.
    from repowise.core.pipeline.cleanup_debt import clear_cleanup_debt

    clear_cleanup_debt(Path(ctx.repo_path), "vectors", ctx.vector_cleanup_ids)


async def delete_vector_rows(ctx: IncrementalPersist) -> None:
    from repowise.core.pipeline.cleanup_debt import clear_cleanup_debt

    with timed(ctx.timings, "persist.vectors"):
        await ctx.vector_store.delete_many(sorted(ctx.vector_cleanup_ids))
    clear_cleanup_debt(Path(ctx.repo_path), "vectors", ctx.vector_cleanup_ids)


def _lance_dir(ctx: IncrementalPersist) -> Path:
    return Path(ctx.repo_path) / ".repowise" / "lancedb"


def _needs_vector_store(ctx: IncrementalPersist) -> bool:
    return bool(ctx.vector_cleanup_ids) and ctx.vector_store is None


def _record_debt(kind: str) -> Callable[[IncrementalPersist], None]:
    """Keep the ids a failed cleanup could not remove, for the next run to retry."""

    def record(ctx: IncrementalPersist) -> None:
        from repowise.core.pipeline.cleanup_debt import record_cleanup_debt

        ids = ctx.fts_cleanup_ids if kind == "fts" else ctx.vector_cleanup_ids
        record_cleanup_debt(Path(ctx.repo_path), kind, ids)

    return record


CLEANUP_STEPS: tuple[Step, ...] = (
    # Range-scoped for the tombstone half: mark_tombstone_pages re-marks and
    # re-returns pages that are already tombstones, so a widened re-run
    # regenerates these ids and retries the delete. The swept half is not
    # repairable this way, because the sweeps deleted those rows and a later
    # run finds nothing to sweep. Tagging still recovers strictly more than
    # not tagging.
    Step(
        "Tombstone full-text removal",
        delete_fts_rows,
        when=lambda ctx: bool(ctx.fts_cleanup_ids),
        range_scoped=True,
        on_error=_record_debt("fts"),
    ),
    Step(
        "Tombstone vector-store open",
        open_vector_store,
        when=lambda ctx: _needs_vector_store(ctx) and _lance_dir(ctx).exists(),
        range_scoped=True,
        on_error=_record_debt("vectors"),
    ),
    Step(
        "Tombstone vector debt clear",
        clear_vector_debt_without_store,
        when=lambda ctx: _needs_vector_store(ctx) and not _lance_dir(ctx).exists(),
        guarded=False,
    ),
    Step(
        "Tombstone vector removal",
        delete_vector_rows,
        when=lambda ctx: bool(ctx.vector_cleanup_ids) and ctx.vector_store is not None,
        on_error=_record_debt("vectors"),
    ),
)


async def persist_incremental_index(
    repo_path: Any,
    graph_builder: Any,
    git_meta_map: dict,
    dead_code_report: Any,
    partial_health_report: Any,
    changed_paths: list[str],
    *,
    doc_drift_report: Any | None = None,
    current_graph_file_paths: set[str] | None = None,
    file_diffs: list[Any] | None = None,
    knowledge_graph_result: Any | None = None,
    parsed_files: list[Any] | None = None,
    source_map: dict[str, Any] | None = None,
    git_decay_map: dict | None = None,
    full_git_summary: Any | None = None,
    history_window_changed: bool = False,
    reconcile_full_scope: bool = False,
    full_generation_page_ids: set[str] | None = None,
    vector_store: Any | None = None,
    log: LogFn | None = None,
    degraded: list[str] | None = None,
    failed_steps: list[str] | None = None,
    timings: PhaseTimings | None = None,
) -> None:
    """Persist an incremental index refresh (graph + symbols + git + dead-code + health).

    Upsert-only: unchanged files keep their existing rows, unlike
    ``persist_pipeline_result``'s delete-then-insert. State-file updates stay
    with the caller — this writes the DB only.

    ``degraded`` (when supplied) collects a one-line entry for every
    best-effort step that failed, so the caller can render an honest
    completion report instead of silently claiming success.

    ``failed_steps`` (when supplied) collects the names of the failed steps
    whose input was *this commit range* rather than the whole repo. Those are
    the only failures a later run cannot heal on its own: their data is scoped
    to a range the sync pointer is about to move past, so the caller records
    them as a repair marker and re-covers the range next update.

    The test for membership is "would widening the next run's diff base repair
    this, and would nothing else". Steps that re-derive the whole repo every
    run are deliberately absent (graph nodes, the sweeps, the page tree,
    related pages, the knowledge graph, the deleted-file prune): they heal
    themselves on the next update, and marking them would let a permanently
    broken one pin the repair window open. So is the commit capture, which
    looks range-scoped and is not: it bounds its walk by the newest
    ``committed_at`` already in the table, so a run it skipped is re-walked by
    the next one whatever the diff base says.

    ``timings`` records the whole write as ``persist`` and each step as a
    ``persist.*`` row. A step that did not run records nothing, so an absent
    row means skipped rather than free.
    """
    ctx = IncrementalPersist(
        repo_path=repo_path,
        graph_builder=graph_builder,
        git_meta_map=git_meta_map,
        dead_code_report=dead_code_report,
        partial_health_report=partial_health_report,
        changed_paths=changed_paths,
        doc_drift_report=doc_drift_report,
        current_graph_file_paths=current_graph_file_paths,
        file_diffs=file_diffs,
        knowledge_graph_result=knowledge_graph_result,
        parsed_files=parsed_files,
        source_map=source_map,
        git_decay_map=git_decay_map,
        full_git_summary=full_git_summary,
        history_window_changed=history_window_changed,
        reconcile_full_scope=reconcile_full_scope,
        full_generation_page_ids=full_generation_page_ids,
        vector_store=vector_store,
        log=log or _noop_log,
        degraded=degraded,
        failed_steps=failed_steps,
        timings=timings,
    )
    await _write(ctx)


async def _write(ctx: IncrementalPersist) -> None:
    """Open the store, run every step in one transaction, then the cleanup tail."""
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        refresh_planner_stats,
        upsert_repository,
    )
    from repowise.core.persistence.database import resolve_db_url

    timings = ctx.timings
    ctx.engine = create_engine(resolve_db_url(ctx.repo_path))
    if timings is not None:
        timings.start("persist")
    try:
        with timed(timings, "persist.open"):
            await init_db(ctx.engine)
            sf = create_session_factory(ctx.engine)

        async with get_session(sf) as session:
            with timed(timings, "persist.open"):
                repo = await upsert_repository(
                    session,
                    name=ctx.repo_path.name,
                    local_path=str(ctx.repo_path),
                    stamp_head=True,
                )
            ctx.session, ctx.repo_id = session, repo.id
            await run_steps(ctx, INCREMENTAL_STEPS)
            # The transaction commits as the block exits.
            if timings is not None:
                timings.start("persist.commit")
        if timings is not None:
            timings.stop("persist.commit")

        # Every update, not only those with git changes: a backlog an earlier
        # budget left drains here, and an empty window costs one query.
        from repowise.core.pipeline.commit_health import refresh_commit_health_after_update

        with timed(timings, "persist.commit_health"):
            await refresh_commit_health_after_update(sf, ctx.repo_id, str(ctx.repo_path))

        from repowise.core.pipeline.cleanup_debt import load_cleanup_debt

        cleanup_debt = load_cleanup_debt(Path(ctx.repo_path))
        ctx.fts_cleanup_ids = (
            set(ctx.tombstoned_page_ids) | set(ctx.swept_page_ids) | cleanup_debt["fts"]
        )
        ctx.vector_cleanup_ids = (
            set(ctx.tombstoned_page_ids) | set(ctx.deselected_page_ids) | cleanup_debt["vectors"]
        )
        await run_steps(ctx, CLEANUP_STEPS)
        with timed(timings, "persist.planner_stats"):
            await refresh_planner_stats(ctx.engine)

        # Ceiling: retired non-file pages' *vector* embeddings survive this path.
        # There is no store here to delete them from, and building one would
        # pull the lancedb import onto the post-commit hook, which
        # ``deterministic.py`` avoids on purpose — and with the default mock
        # embedder this path never wrote a page embedding in the first place.
        # LanceDB hydrates a hit from its own columns, so a residual embedding
        # can still surface in semantic search until the next docs-mode update
        # (which does delete it) or a reindex.
    finally:
        await ctx.engine.dispose()
        if timings is not None:
            timings.stop("persist")
