"""Analysis phase: the four analyses or their resumed views, the KG skeleton, the ANALYSIS checkpoint."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

# Phase functions are looked up on the orchestrator at call time, so a patch on
# ``repowise.core.pipeline.orchestrator`` reaches this module too.
from repowise.core.pipeline import orchestrator as _orch
from repowise.core.pipeline.phase_timing import timed
from repowise.core.pipeline.phases._common import (
    TEST_RUN_FILE_LIMIT,
    _phase_done,
    limit_to_top_pagerank,
)
from repowise.core.pipeline.phases.analysis import STRUCTURAL_RUN_REASON
from repowise.core.pipeline.progress import STAGE_ANALYSIS, emit_stage
from repowise.core.pipeline.resume import ResumePhase

from .state import PipelineRun

logger = structlog.get_logger("repowise.core.pipeline.orchestrator")


async def run_analysis_phase(run: PipelineRun) -> None:
    """Fill the analysis outputs and the KG skeleton of *run*, then checkpoint them."""
    _limit_test_run(run)
    emit_stage(run.progress, STAGE_ANALYSIS)
    run.skip_analysis = await _rehydrate_analysis(run)
    if not run.skip_analysis:
        await _compute_analysis(run)
    _build_knowledge_graph(run)
    if run.resume_controller is not None and not run.skip_analysis:
        await _checkpoint_analysis(run)


def _limit_test_run(run: PipelineRun) -> None:
    # Same helper as ``run_generation`` so the two ``--test-run`` paths cannot drift.
    if not (run.test_run and run.generate_docs):
        return
    run.parsed_files = limit_to_top_pagerank(
        run.parsed_files, run.graph_builder, n=TEST_RUN_FILE_LIMIT
    )
    if run.progress:
        run.progress.on_message("notice", f"Test run: limiting to {len(run.parsed_files)} files")


async def _rehydrate_analysis(run: PipelineRun) -> bool:
    """Reuse the persisted analysis views generation reads; False to recompute.

    Only the thin generation inputs come back (``health_report`` stays None); the
    persisted rows stay authoritative and are never re-written from these.
    """
    controller = run.resume_controller
    if not (controller and await controller.can_skip(ResumePhase.ANALYSIS)):
        return False
    try:
        (
            run.gen_dead_code_report,
            run.gen_decision_report,
        ) = await controller.rehydrate_analysis()
        if run.progress:
            run.progress.on_message("info", "  ↳ Resuming — reusing persisted analysis")
    except Exception as exc:
        logger.warning("resume_rehydrate_analysis_failed_recomputing", error=str(exc))
        return False
    if not await controller.has_completed_decision_extraction():
        await _backfill_decisions(run)
    return True


def _no_model_reason(run: PipelineRun) -> str | None:
    """Why a deterministic run has no model, for its skipped decision sources."""
    config = run.generation_config
    return STRUCTURAL_RUN_REASON if config is not None and config.deterministic else None


async def _backfill_decisions(run: PipelineRun) -> None:
    """Run decision extraction alone when a resumed analysis never finished it."""
    if run.progress:
        run.progress.on_message(
            "info",
            "  ↳ Resumed analysis missing decision extraction — running extraction",
        )
    run.decision_report = await _orch._run_decision_extraction(
        run.repo_path,
        llm_client=run.llm_client,
        graph_builder=run.graph_builder,
        git_meta_map=run.git_meta_map,
        parsed_files=run.parsed_files,
        source_map=run.source_map,
        progress=run.progress,
        no_model_reason=_no_model_reason(run),
    )
    if run.decision_report is not None:
        run.gen_decision_report = run.decision_report
        await run.resume_controller.checkpoint_decision_backfill(
            run.decision_report, progress=run.progress
        )


async def _compute_analysis(run: PipelineRun) -> None:
    """Run dead code, health, decisions and doc drift concurrently.

    They share read-only inputs and no data, so the LLM-bound decision extraction
    hides behind the CPU-bound dead-code and health work.
    """
    progress = run.progress
    (
        run.dead_code_report,
        run.health_report,
        run.decision_report,
        run.doc_drift_report,
    ) = await asyncio.gather(
        _orch._run_dead_code_analysis(
            run.graph_builder,
            run.git_meta_map,
            source_map=run.source_map,
            repo_path=run.repo_path,
            traversal_stats=run.traversal_stats,
            progress=progress,
        ),
        _orch._run_health_analysis(
            run.graph_builder,
            run.git_meta_map,
            run.parsed_files,
            repo_path=run.repo_path,
            coverage_report_paths=run.coverage_report_paths,
            progress=progress,
        ),
        _orch._run_decision_extraction(
            run.repo_path,
            llm_client=run.llm_client,
            graph_builder=run.graph_builder,
            git_meta_map=run.git_meta_map,
            parsed_files=run.parsed_files,
            source_map=run.source_map,
            progress=progress,
            no_model_reason=_no_model_reason(run),
        ),
        _orch._run_doc_drift_analysis(
            run.source_map,
            file_infos=run.file_infos,
            graph_builder=run.graph_builder,
            repo_path=run.repo_path,
            progress=progress,
        ),
    )
    # Health reads the in-memory BlameIndex while it runs; drop it only after the
    # gather, before it can leak into the result or persistence.
    _orch.drop_transient_git_signals(run.git_metadata_list)
    run.gen_dead_code_report = run.dead_code_report
    run.gen_decision_report = run.decision_report


def _build_knowledge_graph(run: PipelineRun) -> None:
    """Reuse or build the deterministic KG skeleton, then curate it; failures only log."""
    try:
        from repowise.core.analysis.knowledge_graph import (
            KnowledgeGraphResult,
            build_knowledge_graph_skeleton,
            compute_kg_fingerprint,
            should_skip_kg_rebuild,
        )

        new_fingerprint = compute_kg_fingerprint(run.graph_builder)
        kg_json_path = run.repo_path / ".repowise" / "knowledge-graph.json"
        if should_skip_kg_rebuild(run.existing_kg_fingerprint, new_fingerprint, kg_json_path):
            _reuse_kg_artifact(run, KnowledgeGraphResult, kg_json_path, new_fingerprint)

        run.tech_stack_dicts = [
            {"name": t.name, "version": t.version, "category": t.category} for t in run.tech_items
        ]
        if run.knowledge_graph_result is None:
            _build_kg_skeleton(run, build_knowledge_graph_skeleton, new_fingerprint)
        _phase_done(run.progress, "knowledge_graph.skeleton")
        if run.knowledge_graph_result is not None:
            _curate_knowledge_graph(run)
    except (ValueError, KeyError, OSError, RuntimeError) as kg_err:
        logger.error("kg_skeleton_building_failed", error=str(kg_err), exc_info=True)


def _reuse_kg_artifact(
    run: PipelineRun, result_cls: Any, kg_json_path: Any, new_fingerprint: str
) -> None:
    progress = run.progress
    run.knowledge_graph_result = result_cls.from_file(kg_json_path)
    if run.knowledge_graph_result is not None:
        run.knowledge_graph_result.fingerprint = new_fingerprint
        logger.info(
            "knowledge_graph.skip",
            reason="fingerprint_unchanged",
            fingerprint=new_fingerprint,
        )
        if progress:
            progress.on_message(
                "info",
                f"  ↳ KG unchanged (fingerprint {new_fingerprint[:8]}…), reusing",
            )
        return
    # The reader refused a file the fingerprint called reusable (unreadable or an
    # older schema): say so before the rebuild.
    logger.info(
        "knowledge_graph.reuse_failed",
        reason="artifact_unreadable_or_outdated",
        path=str(kg_json_path),
    )
    if progress:
        progress.on_message(
            "info",
            "  ↳ knowledge-graph.json could not be reused, rebuilding it",
        )


def _build_kg_skeleton(run: PipelineRun, build_skeleton: Any, new_fingerprint: str) -> None:
    progress = run.progress
    if progress:
        progress.on_phase_start("knowledge_graph.skeleton", None)
    run.knowledge_graph_result = build_skeleton(
        parsed_files=run.parsed_files,
        graph_builder=run.graph_builder,
        repo_structure=run.repo_structure,
        tech_stack=run.tech_stack_dicts,
        external_systems=run.external_systems,
        git_meta_map=run.git_meta_map,
        dead_code_report=run.gen_dead_code_report,
        repo_path=run.repo_path,
    )
    run.knowledge_graph_result.fingerprint = new_fingerprint
    _phase_done(progress, "knowledge_graph.skeleton")
    if progress:
        kg = run.knowledge_graph_result
        progress.on_message(
            "info",
            f"  ↳ KG skeleton: {len(kg.nodes)} nodes, "
            f"{len(kg.edges)} edges, "
            f"{len(kg.layers)} layers",
        )


def _curate_knowledge_graph(run: PipelineRun) -> None:
    """Reshape the exported KG only (layers, tour, entry points, summaries).

    Never touches the AST graph, communities or centrality; a no-op when
    ``REPOWISE_KG_CURATION`` is falsy. Runs in every mode, before generation.
    """
    from repowise.core.analysis.kg_curation import curate_knowledge_graph, curation_enabled

    try:
        # Generate mode defers the summary floor until after the wiki-page
        # backfill so rich page summaries win; other runs floor here.
        will_generate = run.generate_docs and run.llm_client is not None
        with timed(getattr(run.progress, "table", None), "knowledge_graph.curate"):
            run.knowledge_graph_result = curate_knowledge_graph(
                run.knowledge_graph_result,
                parsed_files=run.parsed_files,
                graph_builder=run.graph_builder,
                repo_structure=run.repo_structure,
                community_info=run.graph_builder.community_info(),
                git_meta_map=run.git_meta_map,
                enabled=curation_enabled(),
                defer_summary_floor=will_generate,
            )
    except (ValueError, KeyError, RuntimeError) as cur_err:
        logger.error("kg_curation_failed", error=str(cur_err), exc_info=True)


async def _checkpoint_analysis(run: PipelineRun) -> None:
    """Persist the analysis so a resume skips it; the first write of each decision.

    The vector store goes with it: only this pass can fold a paraphrased decision
    into an existing one, since by the end-of-run persist every group matches on title.
    """
    with timed(getattr(run.progress, "table", None), "persist.checkpoint_analysis"):
        await run.resume_controller.checkpoint_analysis(
            parsed_files=run.parsed_files,
            dead_code_report=run.dead_code_report,
            health_report=run.health_report,
            decision_report=run.decision_report,
            doc_drift_report=run.doc_drift_report,
            git_metadata_list=run.git_metadata_list,
            vector_store=run.vector_store,
            progress=run.progress,
        )
