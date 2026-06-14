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
) -> None:
    """Persist the index-only update (graph + git + dead-code + health), save
    state, and print the completion line. No LLM regeneration.

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
            log=console.print,
        )
    )
    from repowise.cli.helpers import config_fingerprint

    save_state(
        repo_path,
        {**state, "last_sync_commit": head, "config_fingerprint": config_fingerprint(repo_path)},
    )
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
        from repowise.core.persistence.crud import save_health_findings, save_health_metrics
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

            analyzer = HealthAnalyzer(
                graph_builder.graph(),
                git_meta_map=git_meta_map,
                parsed_files=parsed_files,
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
