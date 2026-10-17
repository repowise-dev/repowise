"""The incremental index writer: every DB step of an update, in order."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repowise.core.pipeline.phase_timing import PhaseTimings, timed

from ._common import LogFn, _noop_log
from .commits import persist_incremental_commits
from .external_systems import refresh_external_systems
from .health import persist_partial_health
from .prune import prune_deleted_files


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
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.database import resolve_db_url

    log = log or _noop_log

    def _skip(step: str, exc: Exception, *, range_scoped: bool = False) -> None:
        log(f"[yellow]{step} skipped: {exc}[/yellow]")
        if degraded is not None:
            degraded.append(f"{step}: {exc}")
        if range_scoped and failed_steps is not None:
            failed_steps.append(step)

    url = resolve_db_url(repo_path)
    engine = create_engine(url)
    # Filled by the tombstone step; read after the session closes, so it has to
    # survive a step that was skipped.
    tombstoned_page_ids: list[str] = []
    # Same contract, for rows of a page that has been retired outright.
    swept_page_ids: list[str] = []
    if timings is not None:
        timings.start("persist")
    try:
        with timed(timings, "persist.open"):
            await init_db(engine)
            sf = create_session_factory(engine)

        async with get_session(sf) as session:
            with timed(timings, "persist.open"):
                repo = await upsert_repository(
                    session, name=repo_path.name, local_path=str(repo_path), stamp_head=True
                )
            repo_id = repo.id

            if reconcile_full_scope:
                try:
                    from repowise.core.pipeline.persist import (
                        reconcile_full_index_scope,
                    )

                    current_graph_paths = set(
                        getattr(
                            graph_builder,
                            "traversed_file_paths",
                            {pf.file_info.path for pf in parsed_files or []},
                        )
                    )
                    current_git_paths = set(git_meta_map)
                    with timed(timings, "persist.scope_reconcile"):
                        tombstoned_page_ids = await reconcile_full_index_scope(
                            session,
                            repo_id,
                            current_graph_paths,
                            current_git_paths,
                        )
                except Exception as exc:
                    _skip("Config scope reconciliation", exc)

            if full_generation_page_ids is not None:
                try:
                    from repowise.core.pipeline.persist import (
                        tombstone_pages_outside_generation,
                    )

                    tombstoned_page_ids += await tombstone_pages_outside_generation(
                        session, repo_id, full_generation_page_ids
                    )
                except Exception as exc:
                    _skip("Generation scope reconciliation", exc)

            # Delete rows of pages retired since this index was built. This
            # path never regenerates a repo-wide page, so nothing else here
            # would ever visit one to notice it should be gone, and for a user
            # whose updates all come from the post-commit hook this is the only
            # place a retirement can land.
            try:
                from repowise.core.pipeline.persist import (
                    sweep_absent_cycle_pages,
                    sweep_absent_symbol_spotlights,
                    sweep_retired_pages,
                )

                with timed(timings, "persist.sweeps"):
                    swept_page_ids = await sweep_retired_pages(session, repo_id)
                    # Same reasoning as the retirement sweep above: this path
                    # never regenerates a cycle page, so asking the rebuilt
                    # graph whether the cycle still exists is the only way a
                    # fixed cycle's page can ever be retired for a user who
                    # only runs `update`. Spotlights of removed symbols too.
                    swept_page_ids += await sweep_absent_cycle_pages(
                        session, repo_id, graph_builder
                    )
                    swept_page_ids += await sweep_absent_symbol_spotlights(
                        session, repo_id, graph_builder
                    )
            except Exception as exc:
                _skip("Retired page sweep", exc)

            # Tombstone pages for deleted/renamed files FIRST — a fresh page
            # for a file that no longer exists misleads every retrieval
            # consumer until the next full regeneration.
            if file_diffs:
                try:
                    from repowise.core.pipeline.persist import (
                        mark_tombstone_pages,
                        tombstone_candidates,
                    )

                    with timed(timings, "persist.tombstones"):
                        tombstoned_page_ids += await mark_tombstone_pages(
                            session, repo_id, tombstone_candidates(file_diffs)
                        )
                except Exception as exc:
                    _skip("Tombstone marking", exc, range_scoped=True)

            # Placement depends on the whole page set, which on an incremental
            # run lives in the store rather than in the pages just generated.
            try:
                from repowise.core.pipeline.page_tree_sync import rebuild_page_tree

                with timed(timings, "persist.page_tree"):
                    await rebuild_page_tree(session, repo_id)
            except Exception as exc:
                _skip("Page tree rebuild", exc)

            if git_meta_map or git_decay_map or full_git_summary is not None:
                try:
                    with timed(timings, "persist.git"):
                        from repowise.core.pipeline.persist import persist_git_refresh

                        await persist_git_refresh(
                            session,
                            repo_id,
                            git_meta_map,
                            git_decay_map,
                            full_git_summary,
                            keep_commit_health=not history_window_changed,
                        )
                except Exception as exc:
                    _skip("Git persist", exc, range_scoped=True)

                try:
                    with timed(timings, "persist.commits"):
                        if full_git_summary is None:
                            await persist_incremental_commits(
                                session, repo_id, repo_path, timings=timings
                            )
                except Exception as exc:
                    _skip("Commit capture", exc)

            if dead_code_report is not None:
                try:
                    from repowise.core.persistence.crud import (
                        replace_dead_code_findings,
                    )

                    # The report is repo-wide, and dead code is a cross-file
                    # property, so a change-scoped write would drop every
                    # verdict the change flipped outside the change set. The
                    # scope is the set of files whose confidence was scored on
                    # real git metadata; the rest keep what they had.
                    with timed(timings, "persist.dead_code"):
                        await replace_dead_code_findings(
                            session,
                            repo_id,
                            dead_code_report.findings,
                            scope=dead_code_report.authoritative_paths,
                        )
                except Exception as exc:
                    _skip("Dead-code persist", exc, range_scoped=True)

            if doc_drift_report is not None:
                try:
                    from repowise.core.persistence.crud import (
                        replace_doc_drift_guarded,
                    )

                    with timed(timings, "persist.doc_drift"):
                        await replace_doc_drift_guarded(
                            session, repo_id, doc_drift_report
                        )
                except Exception as exc:
                    # Not range_scoped: the pass re-derives every document each
                    # run, so a skipped write heals itself on the next update.
                    _skip("Doc-drift persist", exc)

            # Re-persist graph_nodes so symbol-level PageRank /
            # betweenness / community ids stay in sync with the
            # current graph build. Without this, ``repowise update``
            # leaves stale per-symbol metrics from the original init
            # and the UI shows "Not indexed in graph" for every
            # symbol on existing repos. Before health, whose plan
            # annotations read each symbol's ``public_api`` from here; a
            # failed write leaves them reading the last run's flags.
            try:
                from repowise.core.pipeline.persist import persist_graph_nodes

                with timed(timings, "persist.graph_nodes"):
                    await persist_graph_nodes(session, repo_id, graph_builder, timings=timings)
            except Exception as exc:
                _skip("Graph nodes persist", exc)

            if partial_health_report is not None:
                try:
                    with timed(timings, "persist.health"):
                        await persist_partial_health(
                            session, repo_id, partial_health_report, repo_path
                        )
                except Exception as exc:
                    _skip("Health persist", exc, range_scoped=True)

            # Refresh wiki_symbols for the changed files. Historically the
            # incremental path re-parsed but never persisted symbols, so their
            # start/end bounds fossilized at the last full index and the
            # get_answer hydrator served drifted signatures. Scoped to the
            # changed set for cost.
            try:
                from repowise.core.pipeline.persist import persist_incremental_symbols

                with timed(timings, "persist.symbols"):
                    await persist_incremental_symbols(session, repo_id, parsed_files, changed_paths)
            except Exception as exc:
                _skip("Symbol persist", exc, range_scoped=True)

            try:
                from repowise.core.pipeline.persist import persist_incremental_security_findings

                with timed(timings, "persist.security"):
                    await persist_incremental_security_findings(
                        session, repo_id, parsed_files, source_map, changed_paths
                    )
            except Exception as exc:
                _skip("Security persist", exc, range_scoped=True)

            # Refresh graph_edges for the changed files. The full-init path was
            # historically the only writer of edges, so adjacency froze at the
            # last full index: new imports/calls stayed invisible and dropped
            # ones lingered as false paths. Phase E flow-path traversal reads
            # adjacency straight from this table, so it decayed on every update.
            try:
                from repowise.core.pipeline.persist import persist_incremental_edges

                with timed(timings, "persist.edges"):
                    await persist_incremental_edges(
                        session, repo_id, graph_builder, parsed_files, changed_paths
                    )
            except Exception as exc:
                _skip("Graph edges persist", exc, range_scoped=True)

            # Refresh related-pages metadata across the whole wiki. LLM-free,
            # so even index-only updates heal pages generated before the
            # feature shipped (or drifted by new imports) in one run.
            try:
                from repowise.core.generation.related_pages import file_import_edges
                from repowise.core.persistence.crud import backfill_related_pages

                with timed(timings, "persist.related_pages"):
                    changed_rel = await backfill_related_pages(
                        session,
                        repo_id,
                        import_edges=file_import_edges(graph_builder),
                        git_meta_map=git_meta_map,
                        pagerank=graph_builder.pagerank(),
                    )
                if changed_rel:
                    log(f"Related pages refreshed on {changed_rel} pages")
            except Exception as exc:
                _skip("Related-pages backfill", exc)

            if knowledge_graph_result is not None:
                try:
                    from repowise.core.pipeline.persist import persist_kg

                    with timed(timings, "persist.kg"):
                        await persist_kg(knowledge_graph_result, session, repo_id)
                except Exception as exc:
                    _skip("Knowledge-graph persist", exc)

            # Refresh external systems (C4 L1) when a dependency manifest
            # changed — otherwise the architecture panel serves the init-time
            # dep list forever. Gated + no LLM (see refresh_external_systems).
            if file_diffs:
                try:
                    with timed(timings, "persist.external_systems"):
                        await refresh_external_systems(
                            session, repo_id, repo_path, file_diffs, log=log
                        )
                except Exception as exc:
                    _skip("External systems refresh", exc, range_scoped=True)

            # One-shot drain of proposals from the removed code_comment
            # harvest (#751). Runs on the index-only path too, because the
            # post-commit hook's updates never reach the full decision
            # persist. Confirmed/dismissed rows are kept.
            try:
                from repowise.core.persistence.crud import (
                    purge_proposed_decisions_by_source,
                )

                await purge_proposed_decisions_by_source(session, repo_id, "code_comment")
                # A history-window change re-mines these; a walk that only
                # refreshes rows under new rules does not, so it keeps them.
                if full_git_summary is not None and history_window_changed:
                    await purge_proposed_decisions_by_source(
                        session, repo_id, "git_archaeology"
                    )
            except Exception as exc:
                _skip("Decision purge", exc)

            # Last in the session on purpose: everything above upserts, and the
            # git step indexes the changed *paths*, deleted ones included, so
            # the prune has to have the final say on what the store claims.
            await prune_deleted_files(
                session,
                repo_id,
                repo_path,
                graph_builder,
                (
                    current_graph_file_paths
                    if current_graph_file_paths is not None
                    else {pf.file_info.path for pf in parsed_files or []}
                ),
                health_report=partial_health_report,
                skip=_skip,
                log=log,
                degraded=degraded,
                timings=timings,
            )
            # The transaction commits as the block exits.
            if timings is not None:
                timings.start("persist.commit")
        if timings is not None:
            timings.stop("persist.commit")

        # Every update, not only those with git changes: a backlog an earlier
        # budget left drains here, and an empty window costs one query.
        from repowise.core.pipeline.commit_health import refresh_commit_health_after_update

        with timed(timings, "persist.commit_health"):
            await refresh_commit_health_after_update(sf, repo_id, str(repo_path))

        # After the session closes: on SQLite the full-text index shares the
        # database file, so writing to it while the session holds a write lock
        # raises "database is locked".
        #
        # A tombstone can never be an answer — hydration drops it — but
        # retrieval fetches a fixed number of rows before that check runs, so
        # every tombstone left in the index costs a real candidate its slot.
        #
        # A swept page's FTS row is worse than a tombstone: search hydrates
        # title and snippet from the FTS copy itself, so an orphan answers in
        # full while the page it names 404s.
        from repowise.core.pipeline.cleanup_debt import (
            clear_cleanup_debt,
            load_cleanup_debt,
            record_cleanup_debt,
        )

        cleanup_debt = load_cleanup_debt(Path(repo_path))
        fts_cleanup_ids = (
            set(tombstoned_page_ids) | set(swept_page_ids) | cleanup_debt["fts"]
        )
        if fts_cleanup_ids:
            try:
                from repowise.core.persistence.search import FullTextSearch

                fts = FullTextSearch(engine)
                with timed(timings, "persist.fts"):
                    await fts.ensure_index()
                    await fts.delete_many(sorted(fts_cleanup_ids))
                clear_cleanup_debt(Path(repo_path), "fts", fts_cleanup_ids)
            except Exception as exc:
                # Range-scoped for the tombstone half: mark_tombstone_pages
                # re-marks and re-returns pages that are already tombstones, so
                # a widened re-run regenerates these ids and retries the delete.
                # The swept half is not repairable this way, because the sweeps
                # deleted those rows and a later run finds nothing to sweep.
                # Tagging still recovers strictly more than not tagging.
                record_cleanup_debt(Path(repo_path), "fts", fts_cleanup_ids)
                _skip("Tombstone full-text removal", exc, range_scoped=True)

        vector_cleanup_ids = set(tombstoned_page_ids) | cleanup_debt["vectors"]
        if vector_cleanup_ids and vector_store is None:
            lance_dir = Path(repo_path) / ".repowise" / "lancedb"
            if lance_dir.exists():
                try:
                    from repowise.core.persistence.vector_store import LanceDBVectorStore
                    from repowise.core.providers.embedding.base import MockEmbedder

                    # Delete-only access never embeds or checks dimensions.
                    vector_store = LanceDBVectorStore(str(lance_dir), embedder=MockEmbedder())
                except Exception as exc:
                    record_cleanup_debt(
                        Path(repo_path), "vectors", vector_cleanup_ids
                    )
                    _skip("Tombstone vector-store open", exc, range_scoped=True)
            else:
                # No store means there are no vector rows left to clean.
                clear_cleanup_debt(Path(repo_path), "vectors", vector_cleanup_ids)

        if vector_cleanup_ids and vector_store is not None:
            try:
                with timed(timings, "persist.vectors"):
                    await vector_store.delete_many(sorted(vector_cleanup_ids))
                clear_cleanup_debt(Path(repo_path), "vectors", vector_cleanup_ids)
            except Exception as exc:
                record_cleanup_debt(Path(repo_path), "vectors", vector_cleanup_ids)
                _skip("Tombstone vector removal", exc)

        # Ceiling: retired non-file pages' *vector* embeddings survive this path.
        # There is no store here to delete them from, and building one would
        # pull the lancedb import onto the post-commit hook, which
        # ``deterministic.py`` avoids on purpose — and with the default mock
        # embedder this path never wrote a page embedding in the first place.
        # LanceDB hydrates a hit from its own columns, so a residual embedding
        # can still surface in semantic search until the next docs-mode update
        # (which does delete it) or a reindex.
    finally:
        await engine.dispose()
        if timings is not None:
            timings.stop("persist")
