"""Full-replace health re-score of every file, reading git metadata from the store."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._common import LogFn, _noop_log


@dataclass(frozen=True)
class RescoreCoverage:
    """Coverage for a health re-score, and whether it replaces what is stored."""

    coverage_map: dict[str, dict] = field(default_factory=dict)
    files: list[Any] = field(default_factory=list)
    source_format: str | None = None
    # True for a reingestion, including an empty one that must clear stored rows.
    authoritative: bool = False
    # How the reingested report mapped (``CoverageProvenance``); None when reused.
    provenance: Any = None


async def coverage_for_rescore(
    session: Any,
    repo_id: str,
    repo_path: Path,
    parsed_files: list[Any],
) -> RescoreCoverage:
    """Coverage to feed a health re-score, preserved across updates.

    Re-discover and re-resolve the reports when one was written after the
    last ingest (``CoverageConfig.reingest``; ``coverage.reingest_on_update``
    forces it on or off), else reload the rows already persisted.
    """
    from repowise.core.analysis.health.coverage import CoverageConfig
    from repowise.core.persistence.crud import load_coverage_map
    from repowise.core.pipeline.incremental.loaders import coverage_reports_to_reread
    from repowise.core.repo_config import load_repo_config

    cfg = CoverageConfig.from_repo_config(load_repo_config(repo_path))

    # ``None`` keeps the stored rows (e.g. from `coverage add`).
    reports = await coverage_reports_to_reread(session, repo_id, Path(repo_path), cfg)
    if reports is not None:
        # Reingestion is authoritative. Falling back to old rows when a report
        # disappeared (or now maps to nothing) makes this config refresh differ
        # from a clean index and then incorrectly stamps stale data current.
        return _reingested_coverage(repo_path, reports, parsed_files, cfg)

    coverage_map = await load_coverage_map(session, repo_id)
    source_format = next((e["source_format"] for e in coverage_map.values()), None)
    return RescoreCoverage(coverage_map, source_format=source_format)


def _reingested_coverage(
    repo_path: Path, reports: Any, parsed_files: list[Any], cfg: Any
) -> RescoreCoverage:
    """The re-read reports resolved onto this run's files; empty when nothing maps."""
    from repowise.core.analysis.health.coverage import build_coverage_map

    if not reports:
        return RescoreCoverage(authoritative=True)
    resolved, _errors = build_coverage_map(
        repo_path,
        list(reports),
        {pf.file_info.path for pf in parsed_files},
        coverage_format=cfg.format,
        strip_prefix=cfg.strip_prefix,
        path_prefix=cfg.path_prefix,
        report_prefixes=reports,
        ignore=cfg.ignore,
    )
    if not resolved.coverage_map:
        return RescoreCoverage(authoritative=True)
    return RescoreCoverage(
        resolved.coverage_map,
        resolved.files,
        resolved.source_format,
        authoritative=True,
        provenance=resolved.provenance,
    )


async def rescore_health_from_store(
    repo_path: Any,
    graph_builder: Any,
    parsed_files: list,
    exclude_patterns: list[str],
    *,
    log: LogFn | None = None,
    timings: Any = None,
) -> Any:
    """Re-score every file's health against the stored git metadata; return the report.

    Used when a config change or the periodic decay cadence needs *every* file
    re-scored, not just the changed ones. The metadata is already persisted, so
    the analyzer reads it from the store (accurate churn, ownership and
    co-change) instead of a changed-only map. Excluded rows are dropped from
    both the store and the analyzer input; metrics and findings are
    full-replaced so rows for newly excluded files disappear.

    The caller supplies an already-built *graph_builder* / *parsed_files* so the
    graph is rebuilt at most once per update. Stages record ``rescore.*`` rows
    in *timings* (a ``PhaseTimings``), apart from the update's own
    ``analysis.health.*`` pass.
    """
    import pathspec

    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.database import resolve_db_url
    from repowise.core.pipeline.index_persistence import (
        IndexWrite,
        apply_index_persistence,
        publish_index_persistence,
    )
    from repowise.core.pipeline.phase_timing import timed

    log = log or _noop_log
    stage_timings = timings.scoped("rescore.") if timings is not None else None
    exclude_spec = (
        pathspec.PathSpec.from_lines("gitwildmatch", exclude_patterns) if exclude_patterns else None
    )
    engine = create_engine(resolve_db_url(Path(repo_path)))
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
            with timed(stage_timings, "inputs"):
                git_meta_map, excluded_paths = await _load_git_meta(
                    session, repo.id, repo_path, parsed_files, exclude_spec
                )
                stored_blame_findings = await _stored_blame_findings(session, repo.id)
                coverage = await coverage_for_rescore(session, repo.id, repo_path, parsed_files)
            report = _analyze(
                repo_path,
                graph_builder,
                parsed_files,
                git_meta_map,
                stored_blame_findings,
                coverage,
                stage_timings,
            )
            log(
                f"Health re-score: [cyan]{len(parsed_files)} files[/cyan], "
                f"[yellow]{len(report.findings)} findings[/yellow]"
            )
            await _save_rescore(
                session, repo, repo_path, graph_builder, report, coverage, stage_timings
            )
            repo_id = repo.id
            await apply_index_persistence(
                session,
                repo_id,
                IndexWrite("incremental", parsed_files, deleted_paths=tuple(excluded_paths)),
            )
    finally:
        await engine.dispose()
    await publish_index_persistence(repo_id, Path(repo_path))
    return report


async def _load_git_meta(
    session: Any, repo_id: str, repo_path: Any, parsed_files: list, exclude_spec: Any
) -> tuple[dict, list[str]]:
    """Stored git metadata for the analyzer, and the excluded paths deleted from the store."""
    from sqlalchemy import delete, select

    from repowise.core.analysis.health.history_refresh import git_meta_rows_to_map
    from repowise.core.persistence.models import GitMetadata
    from repowise.core.pipeline.resume.rehydrate import (
        attach_commit_set_blame,
        attach_stored_commit_shas,
    )
    from repowise.core.workspace.update.state import read_repo_state

    rows = await session.execute(select(GitMetadata).where(GitMetadata.repository_id == repo_id))
    git_rows = list(rows.scalars().all())

    def excluded(path: str) -> bool:
        return exclude_spec is not None and exclude_spec.match_file(path)

    excluded_paths = [gm.file_path for gm in git_rows if excluded(gm.file_path)]
    if excluded_paths:
        await session.execute(
            delete(GitMetadata).where(
                GitMetadata.repository_id == repo_id,
                GitMetadata.file_path.in_(excluded_paths),
            )
        )
        await session.flush()

    # Every stored column, so a gate added with a new column reaches the
    # detectors here as it does on a full index.
    git_meta_map = git_meta_rows_to_map(gm for gm in git_rows if not excluded(gm.file_path))
    # No blame index here, so Split File reads the stored commit sets,
    # and blames once the candidates stored before the sets existed.
    await attach_stored_commit_shas(session, repo_id, git_meta_map)
    attach_commit_set_blame(
        repo_path,
        git_meta_map,
        parsed_files,
        git_tier=read_repo_state(Path(repo_path)).get("git_tier"),
    )
    return git_meta_map, excluded_paths


async def _stored_blame_findings(session: Any, repo_id: str) -> dict[str, list[Any]]:
    """Stored blame-marker findings by file, so the analyzer can reuse them."""
    from sqlalchemy import select

    from repowise.core.analysis.health.history_refresh import BLAME_MARKERS
    from repowise.core.persistence.models import HealthFinding

    by_file: dict[str, list[Any]] = {}
    for finding in (
        await session.execute(
            select(HealthFinding).where(
                HealthFinding.repository_id == repo_id,
                HealthFinding.biomarker_type.in_(BLAME_MARKERS),
            )
        )
    ).scalars():
        by_file.setdefault(finding.file_path, []).append(finding)
    return by_file


def _analyze(
    repo_path: Any,
    graph_builder: Any,
    parsed_files: list,
    git_meta_map: dict,
    stored_blame_findings: dict[str, list[Any]],
    coverage: RescoreCoverage,
    stage_timings: Any,
) -> Any:
    """The whole-repo health report over the stored history."""
    from repowise.core.analysis.communities import file_community_labels
    from repowise.core.analysis.health import HealthAnalyzer
    from repowise.core.analysis.health.config import HealthConfig

    analyzer = HealthAnalyzer(
        graph_builder.graph(),
        git_meta_map=git_meta_map,
        parsed_files=parsed_files,
        community_label_map=file_community_labels(graph_builder),
        coverage_map=coverage.coverage_map,
        duplication_cache_dir=Path(repo_path) / ".repowise",
        repo_root=repo_path,
        stored_blame_findings=stored_blame_findings,
    )
    hcfg = HealthConfig.load(repo_path)
    analyzer_config = (
        hcfg.to_analyzer_config([pf.file_info.path for pf in parsed_files])
        if (hcfg.disabled_biomarkers or hcfg.rules)
        else None
    )
    return analyzer.analyze(analyzer_config, timings=stage_timings)


async def _save_rescore(
    session: Any,
    repo: Any,
    repo_path: Any,
    graph_builder: Any,
    report: Any,
    coverage: RescoreCoverage,
    stage_timings: Any,
) -> None:
    """Write the report with the full-index writer, then what it derives from."""
    from repowise.core.persistence.crud import upsert_git_function_blame_bulk
    from repowise.core.pipeline.persist import (
        persist_graph_nodes,
        save_full_health_report,
    )
    from repowise.core.pipeline.phase_timing import timed
    from repowise.core.workspace.update import get_head_commit

    # Graph nodes first: the plan annotations the writer below stores
    # read each symbol's ``public_api`` from them.
    with timed(stage_timings, "persist.graph_nodes"):
        await persist_graph_nodes(session, repo.id, graph_builder)

    # Same writer the index's analysis phase uses, so a re-score leaves the
    # derived queues agreeing with the findings it just replaced. HEAD comes
    # off disk: this pass scored the working tree, and the stored column is
    # written by a step whose ordering is not guaranteed. It replaces every
    # open finding, the governance ones too, and rebuilds those before folding
    # the refactoring queue once.
    with timed(stage_timings, "persist.health"):
        await save_full_health_report(
            session,
            repo.id,
            report,
            analyzed_commit=get_head_commit(Path(repo_path)),
            repo_path=repo_path,
            refresh_governance=True,
        )
    # Rows for the files blamed above, so the next re-score reads them.
    if report.function_blame_rows:
        await upsert_git_function_blame_bulk(session, repo.id, report.function_blame_rows)
    if coverage.authoritative:
        await _save_reingested_coverage(session, repo, repo_path, coverage)


async def _save_reingested_coverage(
    session: Any, repo: Any, repo_path: Any, coverage: RescoreCoverage
) -> None:
    """Store the re-read coverage, stamped with the commit it measured."""
    from repowise.core.analysis.health.coverage import measurement_commit
    from repowise.core.persistence.crud import save_coverage_files
    from repowise.core.workspace.update import get_head_commit

    # The live HEAD, not ``repo.head_commit``: that names the last indexed
    # commit, and the coverage just scored describes the working tree
    # (issue #1747). A report older than the last commit measures the tree as
    # of its own date, so the stamp moves back to that commit (issue #3901).
    live_head = get_head_commit(Path(repo_path)) or getattr(repo, "head_commit", None)
    await save_coverage_files(
        session,
        repo.id,
        coverage.files,
        source_format=coverage.source_format or "lcov",
        ingested_commit_sha=measurement_commit(
            repo_path,
            getattr(coverage.provenance, "measured_at", None),
            live_head,
        ),
        provenance=coverage.provenance,
    )
