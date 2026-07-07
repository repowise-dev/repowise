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

from repowise.cli.helpers import console, run_async, save_state

from .incremental import _build_repo_graph


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

    async def _stamp() -> None:
        from repowise.cli.helpers import get_db_url_for_repo
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            upsert_repository,
        )

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            await upsert_repository(
                session,
                name=Path(repo_path).name,
                local_path=str(repo_path),
                head_commit=head,
            )

    run_async(_stamp())


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
) -> None:
    """Persist the index-only update (graph + git + dead-code + health + KG),
    save state, and print the completion line. No LLM regeneration.

    DB persistence delegates to :mod:`repowise.core.pipeline.incremental`;
    state-file updates and console reporting stay here.
    """
    from repowise.core.pipeline.incremental import persist_incremental_index

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
    save_state(repo_path, new_state)
    elapsed = time.monotonic() - start
    from .reporting import show_index_only_completion

    show_index_only_completion(
        graph_builder=graph_builder,
        dead_code_report=dead_code_report,
        changed_count=len(changed_paths),
        git_files=len(git_meta_map or {}),
        elapsed=elapsed,
    )


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
