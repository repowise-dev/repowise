"""Index phase: ingestion and git history, external systems, the INDEX checkpoint."""

from __future__ import annotations

import asyncio

import structlog

# Phase functions are looked up on the orchestrator at call time, so a patch on
# ``repowise.core.pipeline.orchestrator`` reaches this module too.
from repowise.core.pipeline import orchestrator as _orch
from repowise.core.pipeline.phases._common import _phase_done
from repowise.core.pipeline.progress import STAGE_INGESTION, emit_stage, emit_warning
from repowise.core.pipeline.resume import ResumePhase

from .state import PipelineRun

logger = structlog.get_logger("repowise.core.pipeline.orchestrator")


async def run_index_phase(run: PipelineRun) -> None:
    """Fill the index outputs of *run*, from a resumed checkpoint when one exists."""
    emit_stage(run.progress, STAGE_INGESTION)
    run.skip_index = await _rehydrate_index(run)
    if not run.skip_index:
        await _compute_index(run)
    run.external_systems = await _extract_external_systems(run)
    # Persist now so an interrupted analysis can resume past the index; a
    # rehydrated index is already persisted.
    if run.resume_controller is not None and not run.skip_index:
        await run.resume_controller.checkpoint_index(
            parsed_files=run.parsed_files,
            graph_builder=run.graph_builder,
            git_metadata_list=run.git_metadata_list,
            git_summary=run.git_summary,
            external_systems=run.external_systems,
            source_map=run.source_map,
            progress=run.progress,
        )
    _emit_index_summary(run)


async def _rehydrate_index(run: PipelineRun) -> bool:
    """Reuse a persisted graph + git index and re-parse source only; False to compute.

    Skips the git history walk and the graph centrality kernels, the slow part of a
    first index. Any rehydration error falls back to a full compute.
    """
    controller = run.resume_controller
    if not (controller and await controller.can_skip(ResumePhase.INDEX)):
        return False
    progress = run.progress
    try:
        run.graph_builder, run.git_meta_map = await controller.rehydrate_index(run.repo_path)
        if progress:
            progress.on_message("info", "  ↳ Resuming — reusing persisted graph + git index")
        (
            run.parsed_files,
            run.file_infos,
            run.repo_structure,
            run.source_map,
            run.tech_items,
        ) = await _orch.reparse_for_resume(
            run.repo_path,
            exclude_patterns=run.exclude_patterns,
            include_submodules=run.include_submodules,
            include_nested_repos=run.include_nested_repos,
            skip_tests=run.skip_tests,
            skip_infra=run.skip_infra,
            progress=progress,
            derive_environment_facts=run.derive_environment_facts,
        )
        run.traversal_stats = None
        run.graph_builder.restore_parse_only_attrs(run.parsed_files)
        # Rehydrated rows can predate the structural label, and nothing
        # else on this path recomputes it.
        _orch.label_co_change_structure(run.graph_builder, run.git_meta_map)
        run.git_metadata_list = list(run.git_meta_map.values())
    except Exception as exc:
        logger.warning("resume_rehydrate_failed_recomputing", error=str(exc))
        return False
    return True


async def _compute_index(run: PipelineRun) -> None:
    """Parse + build the graph and walk git history concurrently (they share no data).

    Rehydrated graphs already carry co-change edges, so only this path adds them.
    """
    (
        (
            run.parsed_files,
            run.file_infos,
            run.repo_structure,
            run.source_map,
            run.graph_builder,
            run.traversal_stats,
            run.tech_items,
        ),
        (
            run.git_summary,
            run.git_metadata_list,
            run.git_meta_map,
        ),
    ) = await asyncio.gather(
        _orch._run_ingestion(
            run.repo_path,
            exclude_patterns=run.exclude_patterns,
            include_submodules=run.include_submodules,
            include_nested_repos=run.include_nested_repos,
            skip_tests=run.skip_tests,
            skip_infra=run.skip_infra,
            progress=run.progress,
            derive_environment_facts=run.derive_environment_facts,
        ),
        _orch._run_git_indexing(
            run.repo_path,
            commit_depth=run.commit_depth,
            follow_renames=run.follow_renames,
            tier=run.git_tier,
            exclude_patterns=run.exclude_patterns,
            progress=run.progress,
        ),
    )
    if run.git_meta_map:
        _orch.label_co_change_structure(run.graph_builder, run.git_meta_map)
        run.graph_builder.add_co_change_edges(run.git_meta_map)


async def _extract_external_systems(run: PipelineRun) -> list[dict]:
    """Declared third-party dependencies from repo manifests (C4 L1); empty on failure."""
    progress = run.progress
    external_systems: list[dict] = []
    if progress:
        progress.on_phase_start("external_systems", None)
    try:
        from repowise.core.ingestion.external_systems import extract_external_systems

        records = await asyncio.to_thread(extract_external_systems, run.repo_path)
        external_systems = [
            {
                "name": r.name,
                "display_name": r.display_name,
                "ecosystem": r.ecosystem,
                "category": r.category,
                "io_kind": r.io_kind,
                "version": r.version,
                "declared_in": r.declared_in,
                "is_dev_dep": r.is_dev_dep,
            }
            for r in records
        ]
        if progress and external_systems:
            progress.on_message(
                "info",
                f"→ External systems: {len(external_systems):,} declared deps across manifests",
            )
    except Exception as _ext_err:
        logger.warning("external_systems_extraction_failed", error=str(_ext_err))
        emit_warning(
            progress,
            f"External dependency manifests not parsed ({_ext_err}); "
            "pages will not name this repo's third-party systems.",
        )
    _phase_done(progress, "external_systems")
    return external_systems


def _emit_index_summary(run: PipelineRun) -> None:
    progress = run.progress
    if not progress:
        return
    _g = run.graph_builder.graph()
    progress.on_message(
        "info",
        f"→ {len(run.parsed_files):,} files parsed · "
        f"{sum(pf.symbol_count for pf in run.parsed_files):,} symbols extracted",
    )
    progress.on_message(
        "info",
        f"→ Graph: {_g.number_of_nodes():,} nodes · {_g.number_of_edges():,} edges",
    )
    git_summary = run.git_summary
    if git_summary and git_summary.files_indexed:
        _hotspot_msg = ""
        if hasattr(git_summary, "hotspots") and git_summary.hotspots:
            _hotspot_msg = f" · {git_summary.hotspots} hotspots"
        progress.on_message(
            "info",
            f"→ Git: {git_summary.files_indexed:,} files indexed{_hotspot_msg}",
        )
