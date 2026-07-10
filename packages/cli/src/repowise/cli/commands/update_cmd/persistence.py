"""Persistence + health-rescore helpers for ``repowise update``.

Holds the index-only persistence path, the config-triggered full health
re-score, and the small ORM->dict adapter. DB writes delegate to
:mod:`repowise.core.pipeline.incremental` / ``repowise.core.persistence``;
state-file updates and console reporting stay here.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import structlog

from repowise.cli.helpers import console, run_async, save_state

from .incremental import _build_repo_graph

log = structlog.get_logger(__name__)


async def _coverage_for_rescore(
    session: Any,
    repo_id: str,
    repo_path: Path,
    parsed_files: list[Any],
) -> tuple[dict[str, dict], list[Any], str | None]:
    """Coverage to feed a health re-score, preserved across updates.

    Default: reload the rows already persisted (no re-parse). When
    ``coverage.reingest_on_update`` is set, re-discover and re-resolve a
    fresh report instead. Returns ``(coverage_map, files_to_persist,
    source_format)`` — ``files_to_persist`` is empty on the reload path
    (rows are unchanged) and populated when re-ingested.
    """
    import json

    from repowise.core.analysis.health.coverage import (
        CoverageConfig,
        build_coverage_map,
        discover_artifacts,
    )
    from repowise.core.persistence.crud import load_coverage_for_repo
    from repowise.core.repo_config import load_repo_config

    cfg = CoverageConfig.from_repo_config(load_repo_config(repo_path))

    if cfg.reingest_on_update and cfg.auto_discover:
        report_paths = discover_artifacts(repo_path, globs=cfg.artifacts or None)
        if report_paths:
            repo_keys = {pf.file_info.path for pf in parsed_files}
            resolved, _errors = build_coverage_map(
                repo_path,
                report_paths,
                repo_keys,
                coverage_format=cfg.format,
                strip_prefix=cfg.strip_prefix,
                path_prefix=cfg.path_prefix,
            )
            if resolved.coverage_map:
                return resolved.coverage_map, resolved.files, resolved.source_format

    rows = await load_coverage_for_repo(session, repo_id)
    coverage_map: dict[str, dict] = {}
    source_format: str | None = None
    for row in rows:
        source_format = source_format or getattr(row, "source_format", None)
        try:
            covered = json.loads(row.covered_lines_json) if row.covered_lines_json else []
        except (ValueError, TypeError):
            covered = []
        coverage_map[row.file_path] = {
            "line_coverage_pct": row.line_coverage_pct,
            "branch_coverage_pct": row.branch_coverage_pct,
            "covered_lines": covered,
            "total_coverable_lines": row.total_coverable_lines or 0,
            "source_format": source_format,
        }
    return coverage_map, [], source_format


async def _persist_partial_health(session: Any, repo_id: str, report: Any) -> None:
    """Upsert health findings + metrics for the changed-files subset.

    Delegates to :mod:`repowise.core.pipeline.incremental` — the logic moved
    to core so workspace updates can reuse the incremental path.
    """
    from repowise.core.pipeline.incremental import persist_partial_health

    await persist_partial_health(session, repo_id, report)


async def _persist_incremental_commits(session: Any, repo_id: str, repo_path: Any) -> None:
    """Capture + upsert ``git_commits`` rows for commits new since the last index.

    Delegates to :mod:`repowise.core.pipeline.incremental`.
    """
    from repowise.core.pipeline.incremental import persist_incremental_commits

    await persist_incremental_commits(session, repo_id, repo_path)


def stamp_head_commit(repo_path: Any, head: str | None) -> None:
    """Advance the persisted ``repositories.head_commit`` to *head*.

    The "no changed files" and "already up to date" fast paths in
    ``update_command`` write ``state.json`` and return without touching the DB.
    But the server's ``/repos`` endpoint and the MCP ``_meta`` freshness check
    read the indexed commit from the ``repositories`` row, not from
    ``state.json`` — so skipping this write pinned the freshness signal at the
    last full index, keeping "index behind checkout" stuck after a successful
    update. Keep the DB stamp in lockstep with ``state.json``. Also self-heals
    a row left stale by a pre-fix run: any later update re-stamps it.
    """
    if not head:
        return

    # One stamper for both update paths: delegate to the core implementation
    # the workspace updater uses. It touches only head_commit/updated_at on an
    # existing row (the old upsert here clobbered url/default_branch with
    # defaults), creates the row when missing from an existing wiki.db, and
    # no-ops when wiki.db itself is absent instead of conjuring an empty DB.
    from repowise.core.workspace.update import reconcile_repo_head_commit

    run_async(reconcile_repo_head_commit(Path(repo_path), head))


def _persist_index_only_update(
    repo_path: Any,
    graph_builder: Any,
    git_meta_map: dict,
    dead_code_report: Any,
    partial_health_report: Any,
    state: dict,
    head: str | None,
    start: float,
    changed_paths: list[str],
    file_diffs: list | None = None,
    knowledge_graph_result: Any | None = None,
    degraded: list[str] | None = None,
) -> None:
    """Persist the index-only update (graph + git + dead-code + health + KG),
    save state, and print the completion line. No LLM regeneration.

    DB persistence delegates to :mod:`repowise.core.pipeline.incremental`;
    state-file updates and console reporting stay here. Best-effort steps
    that fail land in ``degraded`` and surface in the completion panel.
    """
    from repowise.core.pipeline.incremental import persist_incremental_index

    degraded = degraded if degraded is not None else []
    run_async(
        persist_incremental_index(
            repo_path,
            graph_builder,
            git_meta_map,
            dead_code_report,
            partial_health_report,
            changed_paths,
            file_diffs=file_diffs,
            knowledge_graph_result=knowledge_graph_result,
            log=console.print,
            degraded=degraded,
        )
    )
    from repowise.cli.helpers import config_fingerprint

    new_state = {
        **state,
        "last_sync_commit": head,
        "config_fingerprint": config_fingerprint(repo_path),
    }
    if knowledge_graph_result is not None:
        try:
            from repowise.cli.state_persistence import build_kg_state, save_knowledge_graph_json

            save_knowledge_graph_json(repo_path, knowledge_graph_result)
            new_state["knowledge_graph"] = build_kg_state(knowledge_graph_result)
        except Exception as exc:
            console.print(f"[yellow]Knowledge-graph export skipped: {exc}[/yellow]")
            degraded.append(f"Knowledge-graph export: {exc}")
    save_state(repo_path, new_state)
    elapsed = time.monotonic() - start
    from .reporting import show_index_only_completion

    show_index_only_completion(
        graph_builder=graph_builder,
        dead_code_report=dead_code_report,
        changed_count=len(changed_paths),
        git_files=len(git_meta_map or {}),
        elapsed=elapsed,
        degraded=degraded,
    )


class PageCheckpointer:
    """Best-effort per-page persistence during update-time generation.

    The final persist runs only after ALL pages are generated, so a crash
    midway used to re-bill every already-generated page on the next run:
    nothing durable recorded them. This sink upserts each page's DB row as
    it lands; on the rerun the generator's prompt-hash skip sees the
    persisted content and never re-calls the LLM for it.

    Purely a checkpoint: the final persist re-upserts every page anyway
    (idempotent), so any failure here flips the sink off, records one
    degraded entry, and the run continues exactly as before. Writes are
    serialized through a queue consumed by a single task, one short
    transaction per page, on a dedicated engine — generation itself only
    writes the vector store, so wiki.db is uncontended during this window.
    """

    def __init__(self, repo_path: Any, repo_name: str) -> None:
        self.repo_path = repo_path
        self.repo_name = repo_name
        self.failure: str | None = None
        self.persisted = 0
        self._queue: Any | None = None
        self._task: Any | None = None
        self._engine: Any | None = None
        self._session_factory: Any | None = None

    async def start(self) -> None:
        import asyncio

        try:
            from repowise.cli.helpers import get_db_url_for_repo
            from repowise.core.persistence import create_engine, create_session_factory

            self._engine = create_engine(get_db_url_for_repo(self.repo_path))
            self._session_factory = create_session_factory(self._engine)
            self._queue = asyncio.Queue()
            self._task = asyncio.create_task(self._drain())
        except Exception as exc:
            self.failure = str(exc)

    def on_page_ready(self, page: Any) -> None:
        """Sync callback handed to ``generate_all`` — enqueue, never block."""
        if self._queue is not None and self.failure is None:
            self._queue.put_nowait(page)

    async def _drain(self) -> None:
        from repowise.core.persistence import (
            get_session,
            upsert_page_from_generated,
            upsert_repository,
        )

        repo_id: str | None = None
        while True:
            page = await self._queue.get()
            if page is None:
                return
            if self.failure is not None:
                continue  # keep consuming so close() never hangs
            try:
                async with get_session(self._session_factory) as session:
                    if repo_id is None:
                        repo = await upsert_repository(
                            session, name=self.repo_name, local_path=str(self.repo_path)
                        )
                        repo_id = repo.id
                    await upsert_page_from_generated(session, page, repo_id)
                self.persisted += 1
            except Exception as exc:
                self.failure = str(exc)
                log.warning("page_checkpoint_disabled", error=str(exc))

    async def close(self) -> None:
        if self._queue is not None and self._task is not None:
            self._queue.put_nowait(None)
            await self._task
        if self._engine is not None:
            await self._engine.dispose()


def _persist_full_update(
    *,
    repo_path: Any,
    repo_name: str,
    generated_pages: list,
    file_diffs: list,
    git_meta_map: dict,
    new_decision_markers: list,
    decision_vector_store: Any | None,
    provider: Any,
    partial_health_report: Any,
    dead_code_report: Any,
    graph_builder: Any,
    knowledge_graph_result: Any | None,
    degraded: list[str],
) -> None:
    """Persist a full (LLM-regenerating) update in one transaction.

    Mirrors :func:`repowise.core.pipeline.incremental.persist_incremental_index`:
    one engine, one session, so a crash rolls the whole batch back instead of
    leaving pages committed but health/git/graph rows from the previous
    commit (the old shape here was eight separate auto-committing sessions).
    Page upserts fail loudly — pages are the point of docs mode; every other
    step degrades into ``degraded`` with a logged warning. FTS indexing stays
    outside the transaction: it is rebuildable and non-transactional anyway.
    """
    run_async(
        _persist_full_update_async(
            repo_path=repo_path,
            repo_name=repo_name,
            generated_pages=generated_pages,
            file_diffs=file_diffs,
            git_meta_map=git_meta_map,
            new_decision_markers=new_decision_markers,
            decision_vector_store=decision_vector_store,
            provider=provider,
            partial_health_report=partial_health_report,
            dead_code_report=dead_code_report,
            graph_builder=graph_builder,
            knowledge_graph_result=knowledge_graph_result,
            degraded=degraded,
        )
    )


async def _persist_full_update_async(
    *,
    repo_path: Any,
    repo_name: str,
    generated_pages: list,
    file_diffs: list,
    git_meta_map: dict,
    new_decision_markers: list,
    decision_vector_store: Any | None,
    provider: Any,
    partial_health_report: Any,
    dead_code_report: Any,
    graph_builder: Any,
    knowledge_graph_result: Any | None,
    degraded: list[str],
) -> None:
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import (
        FullTextSearch,
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_page_from_generated,
        upsert_repository,
    )

    def _skip(step: str, exc: Exception) -> None:
        log.warning("update_persist_step_degraded", step=step, error=str(exc))
        degraded.append(f"{step}: {exc}")

    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    try:
        await init_db(engine)
        sf = create_session_factory(engine)

        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_name, local_path=str(repo_path))
            repo_id = repo.id

            # Pages first and without a net: everything else is derived
            # metadata, but a docs-mode update that can't write pages failed.
            for page in generated_pages:
                await upsert_page_from_generated(session, page, repo_id)

            # Tombstone pages for deleted/renamed files — regeneration only
            # rewrites pages for files that still exist.
            try:
                from repowise.core.pipeline.persist import (
                    mark_tombstone_pages,
                    tombstone_candidates,
                )

                await mark_tombstone_pages(session, repo_id, tombstone_candidates(file_diffs))
            except Exception as exc:
                _skip("Tombstone marking", exc)

            # Refreshed knowledge graph — same writers as the init pipeline
            # (full-replace layers/tour/curated meta).
            if knowledge_graph_result is not None:
                try:
                    from repowise.core.pipeline.persist import persist_kg

                    await persist_kg(knowledge_graph_result, session, repo_id)
                except Exception as exc:
                    _skip("Knowledge-graph persist", exc)

            # Updated git metadata + recomputed percentiles + new commit rows.
            if git_meta_map:
                try:
                    from repowise.core.persistence.crud import (
                        recompute_git_percentiles,
                        upsert_git_metadata_bulk,
                    )

                    await upsert_git_metadata_bulk(session, repo_id, list(git_meta_map.values()))
                    await recompute_git_percentiles(session, repo_id)
                except Exception as exc:
                    _skip("Git persist", exc)
                try:
                    await _persist_incremental_commits(session, repo_id, repo_path)
                except Exception as exc:
                    _skip("Commit capture", exc)

            # Decision records: new markers + harvested decisions, supersession
            # detection, staleness recompute.
            try:
                decision_dicts: list[dict] = []
                if new_decision_markers:
                    import dataclasses as _dc

                    decision_dicts.extend(_dc.asdict(d) for d in new_decision_markers)
                for page in generated_pages:
                    harvested = page.metadata.get("harvested_decisions")
                    if harvested:
                        decision_dicts.extend(harvested)

                if decision_dicts:
                    from repowise.core.persistence.crud import bulk_upsert_decisions

                    touched_ids = await bulk_upsert_decisions(
                        session,
                        repo_id,
                        decision_dicts,
                        vector_store=decision_vector_store,
                    )
                    if touched_ids and decision_vector_store is not None:
                        from repowise.core.analysis.decision_evolution import (
                            detect_supersessions_and_conflicts,
                        )

                        await detect_supersessions_and_conflicts(
                            session,
                            repo_id,
                            touched_ids=touched_ids,
                            vector_store=decision_vector_store,
                            provider=provider,
                        )

                if git_meta_map:
                    from repowise.core.persistence.crud import recompute_decision_staleness

                    await recompute_decision_staleness(session, repo_id, git_meta_map)
            except Exception as exc:
                _skip("Decision persist", exc)

            # Governance findings pass: runs after decisions + staleness.
            try:
                from sqlalchemy import select as _sel_dec

                from repowise.core.analysis.health.governance import build_governance_findings
                from repowise.core.persistence.crud import (
                    get_decision_health_summary,
                    replace_governance_findings,
                )
                from repowise.core.persistence.models import DecisionRecord

                _dr = await session.execute(
                    _sel_dec(DecisionRecord).where(DecisionRecord.repository_id == repo_id)
                )
                _decisions = list(_dr.scalars().all())
                _summary = await get_decision_health_summary(session, repo_id)
                _gov = build_governance_findings(
                    health_summary=_summary,
                    decisions=_decisions,
                )
                await replace_governance_findings(session, repo_id, _gov)
            except Exception as exc:
                _skip("Governance findings", exc)

            # Code-health findings + metrics (partial — upsert only).
            if partial_health_report is not None:
                try:
                    await _persist_partial_health(session, repo_id, partial_health_report)
                except Exception as exc:
                    _skip("Health persist", exc)

            # Scoped to changed files so unchanged files keep their findings (#295).
            if dead_code_report is not None:
                try:
                    import dataclasses as _dc_dead

                    from repowise.core.persistence.crud import upsert_dead_code_findings

                    await upsert_dead_code_findings(
                        session,
                        repo_id,
                        [_dc_dead.asdict(f) for f in dead_code_report.findings],
                        file_paths=[fd.path for fd in file_diffs],
                    )
                except Exception as exc:
                    _skip("Dead-code persist", exc)

            # Re-persist graph_nodes so symbol-level PageRank / betweenness /
            # community ids reflect the current build.
            try:
                from repowise.core.pipeline.persist import persist_graph_nodes

                await persist_graph_nodes(session, repo_id, graph_builder)
            except Exception as exc:
                _skip("Graph nodes persist", exc)

            # Record a GenerationJob so the web UI "last synced" timestamp updates.
            try:
                from datetime import UTC as _UTC
                from datetime import datetime

                from repowise.core.persistence.crud import upsert_generation_job

                now = datetime.now(_UTC)
                page_count = len(generated_pages)
                job = await upsert_generation_job(
                    session,
                    repository_id=repo_id,
                    status="completed",
                    total_pages=page_count,
                    config={"mode": "incremental", "source": "cli_update"},
                )
                job.completed_pages = page_count
                job.started_at = now
                job.finished_at = now
            except Exception as exc:
                _skip("Generation job record", exc)

        # FTS outside the transaction — rebuildable, and its writer manages
        # its own connection state.
        try:
            fts = FullTextSearch(engine)
            await fts.ensure_index()
            for page in generated_pages:
                await fts.index(page.page_id, page.title, page.content)
        except Exception as exc:
            _skip("Full-text search indexing", exc)
    finally:
        await engine.dispose()


def _git_metadata_to_dict(gm: Any) -> dict[str, Any]:
    """Convert a GitMetadata ORM row to the dict format HealthAnalyzer expects."""
    return {
        "file_path": gm.file_path,
        "commit_count_total": gm.commit_count_total,
        "commit_count_90d": gm.commit_count_90d,
        "commit_count_30d": gm.commit_count_30d,
        "first_commit_at": gm.first_commit_at,
        "last_commit_at": gm.last_commit_at,
        "primary_owner_name": gm.primary_owner_name,
        "primary_owner_email": gm.primary_owner_email,
        "primary_owner_commit_pct": gm.primary_owner_commit_pct,
        "top_authors_json": gm.top_authors_json,
        "significant_commits_json": gm.significant_commits_json,
        "co_change_partners_json": gm.co_change_partners_json,
        "commit_categories_json": gm.commit_categories_json,
        "is_hotspot": gm.is_hotspot,
        "is_stable": gm.is_stable,
        "churn_percentile": gm.churn_percentile,
        "age_days": gm.age_days,
        "commit_count_capped": gm.commit_count_capped,
        "lines_added_90d": gm.lines_added_90d,
        "lines_deleted_90d": gm.lines_deleted_90d,
        "avg_commit_size": gm.avg_commit_size,
        "recent_owner_name": gm.recent_owner_name,
        "recent_owner_commit_pct": gm.recent_owner_commit_pct,
        "bus_factor": gm.bus_factor,
        "contributor_count": gm.contributor_count,
        "original_path": gm.original_path,
        "merge_commit_count_90d": gm.merge_commit_count_90d,
        "temporal_hotspot_score": gm.temporal_hotspot_score,
        "prior_defect_count": gm.prior_defect_count,
        "change_entropy": gm.change_entropy,
        "change_entropy_pct": gm.change_entropy_pct,
    }


def _run_full_health_rescore(
    repo_path: Any,
    exclude_patterns: list[str],
    state: dict,
    head: str | None,
    curr_fingerprint: str,
) -> None:
    """Rebuild graph and re-run full health analysis when config changed.

    Uses save_health_metrics / save_health_findings (full replace, not upsert)
    so rows for newly-excluded files are removed. Loads GitMetadata from the DB
    (so biomarkers keep accurate churn/ownership/co-change data) and removes
    excluded rows both from the DB and the analyzer input.
    """
    import time

    start = time.monotonic()

    import pathspec

    # Share the rebuild path with the incremental update so both produce the
    # same graph (same parser, same framework-aware synthetic edges).
    parsed_files, _source_map, graph_builder, _repo_structure, _file_count = _build_repo_graph(
        repo_path,
        exclude_patterns,
        include_submodules=bool(state.get("include_submodules", False)),
        include_nested_repos=bool(state.get("include_nested_repos", False)),
    )

    # Fan-out metric precompute (mirrors _rebuild_graph_and_git) — the
    # rescore persists graph nodes too, which reads every metric.
    try:
        run_async(graph_builder.compute_metrics_parallel())
    except Exception:
        pass  # metrics fall back to lazy computation

    exclude_spec = (
        pathspec.PathSpec.from_lines("gitwildmatch", exclude_patterns) if exclude_patterns else None
    )

    async def _rescore() -> None:
        from sqlalchemy import delete, select

        from repowise.cli.helpers import get_db_url_for_repo
        from repowise.core.analysis.health import HealthAnalyzer
        from repowise.core.analysis.health.config import HealthConfig
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            upsert_repository,
        )
        from repowise.core.persistence.crud import (
            save_coverage_files,
            save_health_findings,
            save_health_metrics,
        )
        from repowise.core.persistence.models import GitMetadata
        from repowise.core.pipeline.persist import persist_graph_nodes

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)

        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
            repo_id = repo.id

            gm_result = await session.execute(
                select(GitMetadata).where(GitMetadata.repository_id == repo_id)
            )
            git_rows = list(gm_result.scalars().all())
            excluded_git_paths = [
                gm.file_path
                for gm in git_rows
                if exclude_spec is not None and exclude_spec.match_file(gm.file_path)
            ]
            if excluded_git_paths:
                await session.execute(
                    delete(GitMetadata).where(
                        GitMetadata.repository_id == repo_id,
                        GitMetadata.file_path.in_(excluded_git_paths),
                    )
                )
                await session.flush()

            git_meta_map = {
                gm.file_path: _git_metadata_to_dict(gm)
                for gm in git_rows
                if exclude_spec is None or not exclude_spec.match_file(gm.file_path)
            }

            # Preserve coverage across a re-score. The previous behaviour
            # rebuilt the analyzer with no coverage_map, nulling every file's
            # line/branch coverage even though the coverage_files rows still
            # existed. Reload them (and optionally re-discover a fresh report)
            # so coverage survives `repowise update`.
            coverage_map, coverage_files, coverage_format = await _coverage_for_rescore(
                session, repo_id, repo_path, parsed_files
            )

            analyzer = HealthAnalyzer(
                graph_builder.graph(),
                git_meta_map=git_meta_map,
                parsed_files=parsed_files,
                coverage_map=coverage_map,
                duplication_cache_dir=Path(repo_path) / ".repowise",
            )
            hcfg = HealthConfig.load(repo_path)
            analyzer_config = (
                hcfg.to_analyzer_config([pf.file_info.path for pf in parsed_files])
                if (hcfg.disabled_biomarkers or hcfg.rules)
                else None
            )
            report = analyzer.analyze(analyzer_config)

            console.print(
                f"Health re-score: [cyan]{len(parsed_files)} files[/cyan], "
                f"[yellow]{len(report.findings)} findings[/yellow]"
            )

            await save_health_metrics(session, repo_id, report.metrics or [])
            await save_health_findings(session, repo_id, list(report.findings or []))
            if coverage_files:
                await save_coverage_files(
                    session,
                    repo_id,
                    coverage_files,
                    source_format=coverage_format or "lcov",
                    ingested_commit_sha=getattr(repo, "head_commit", None),
                )
            await persist_graph_nodes(session, repo_id, graph_builder)

    try:
        run_async(_rescore())
    except Exception as exc:
        # Return without advancing the fingerprint so the next update retries.
        console.print(f"[yellow]Health re-score failed: {exc}[/yellow]")
        return

    save_state(
        repo_path,
        {**state, "last_sync_commit": head, "config_fingerprint": curr_fingerprint},
    )
    elapsed = time.monotonic() - start
    console.print(f"[green]Config-triggered health re-score complete[/green] in {elapsed:.1f}s")
