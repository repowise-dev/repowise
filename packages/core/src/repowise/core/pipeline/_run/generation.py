"""Generation phase: wiki pages with concurrent KG enrichment, then execution flows."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import structlog

# Phase functions are looked up on the orchestrator at call time, so a patch on
# ``repowise.core.pipeline.orchestrator`` reaches this module too.
from repowise.core.pipeline import orchestrator as _orch
from repowise.core.pipeline.phases._common import _phase_done
from repowise.core.pipeline.progress import emit_warning

from .state import PipelineRun

logger = structlog.get_logger("repowise.core.pipeline.orchestrator")


async def run_generation_phase(run: PipelineRun) -> None:
    """Generate pages when the run asks for docs and has a provider; else a no-op."""
    generation_client = _generation_client(run)
    if not (run.generate_docs and generation_client is not None):
        return
    progress = run.progress
    if progress:
        progress.on_message("info", "Phase 3: Generation")
    config = _resolve_generation_config(run)
    _detect_api_contracts(run)
    kg_structural_task = _start_kg_enrichment(run, config)
    try:
        run.generated_pages = await _orch.run_generation(
            repo_path=run.repo_path,
            parsed_files=run.parsed_files,
            source_map=run.source_map,
            graph_builder=run.graph_builder,
            repo_structure=run.repo_structure,
            git_meta_map=run.git_meta_map,
            llm_client=generation_client,
            embedder=run.embedder,
            vector_store=run.vector_store,
            concurrency=run.concurrency,
            progress=progress,
            resume=run.resume,
            generation_config=config,
            dead_code_report=run.gen_dead_code_report,
            decision_report=run.gen_decision_report,
            external_systems=run.external_systems,
            on_page_ready=run.on_page_ready,
            preserved_page_ids=run.preserved_page_ids,
            **_kg_snapshot(run),
        )
    except BaseException:
        # Stop the concurrent enrichment's LLM calls and keep its failure from
        # surfacing as "Task exception was never retrieved" at teardown.
        if kg_structural_task is not None:
            kg_structural_task.cancel()
            with contextlib.suppress(BaseException):
                await kg_structural_task
        raise
    _claim_authoritative_page_types(run)
    if kg_structural_task is not None:
        await _finalize_kg_enrichment(run, kg_structural_task)


def _kg_snapshot(run: PipelineRun) -> dict[str, Any]:
    """The in-memory KG for generation: the artifact file is written after it."""
    kg = run.knowledge_graph_result
    if kg is None:
        return {"kg_modules": None, "kg_data": None}
    return {"kg_modules": kg.modules or None, "kg_data": kg.to_dict()}


def _generation_client(run: PipelineRun) -> Any:
    """The caller's provider, or a template provider for a keyless DETERMINISTIC run.

    Scoped to generation, not assigned over ``llm_client``: decision extraction
    branches on that client's truthiness and would lose its heuristic fallbacks.
    """
    if run.generate_docs and run.mode.deterministic_docs and run.llm_client is None:
        from repowise.core.providers.llm.template import TemplateProvider

        return TemplateProvider()
    return run.llm_client


def _resolve_generation_config(run: PipelineRun) -> Any:
    config = run.generation_config
    if config is None:
        from repowise.core.generation import GenerationConfig
        from repowise.core.reasoning import resolve_reasoning
        from repowise.core.repo_config import load_repo_config

        _cfg = load_repo_config(run.repo_path)
        # Wiki style precedence: explicit param > repo config.yaml > default.
        _style = run.wiki_style or _cfg.get("wiki_style", "comprehensive")
        config = GenerationConfig.from_repo_config(
            _cfg,
            max_concurrency=run.concurrency,
            reasoning=resolve_reasoning(config=_cfg),
            wiki_style=_style,
            language=_cfg.get("language", "en"),
        )
    # The mode decides how pages render: a DETERMINISTIC run with an
    # LLM-shaped config would still prompt.
    if run.mode.deterministic_docs and not config.deterministic:
        from dataclasses import replace as _dc_replace

        config = _dc_replace(config, deterministic=True)
    return config


def _detect_api_contracts(run: PipelineRun) -> None:
    """Flag framework-defined HTTP surfaces so they render as api_contract pages."""
    from repowise.core.generation import detect_code_api_contracts as _detect_apis

    progress = run.progress
    try:
        flipped = _detect_apis(run.parsed_files)
        if flipped and progress:
            progress.on_message("info", f"→ Detected {flipped} additional API contract file(s)")
    except Exception as _api_err:
        logger.warning("api_contract_detection_failed", error=str(_api_err))
        emit_warning(
            progress,
            f"API contract detection skipped ({_api_err}); endpoint pages may be missing.",
        )


def _start_kg_enrichment(run: PipelineRun, config: Any) -> asyncio.Task | None:
    """Launch LLM layer naming + tour to overlap page generation; None when not run.

    Generation holds a ``to_dict()`` snapshot of the skeleton and enrichment reads
    only skeleton layers and warmed graph metrics, so neither sees the other's live
    output; results apply in ``finalize_knowledge_graph`` after generation.
    Skipped in DETERMINISTIC mode, whose null provider raises on any prompt.
    """
    if run.knowledge_graph_result is None or run.mode.deterministic_docs:
        return None
    from repowise.core.generation.knowledge_graph import enrich_knowledge_graph_structural

    _kg_reasoning = getattr(config, "reasoning", "auto") if config else "auto"
    # Warm the pagerank cache on this thread so both concurrent readers only read it.
    run.graph_builder.pagerank()
    return asyncio.create_task(
        enrich_knowledge_graph_structural(
            kg_skeleton=run.knowledge_graph_result,
            llm_client=run.llm_client,
            graph_builder=run.graph_builder,
            repo_structure=run.repo_structure,
            tech_stack=run.tech_stack_dicts,
            reasoning=_kg_reasoning,
        )
    )


def _claim_authoritative_page_types(run: PipelineRun) -> None:
    """Record the structural page types this run fully decided, for the stale-page sweep.

    ``module_page`` is claimed on evidence (a concept page was produced) or for a
    repo with no production code: ``file_pages_only`` and per-page provider errors
    also emit zero, and claiming then would delete every concept page. No run emits
    layer pages (their ids resolve through the redirect table) and SCC pages carry
    no budget, so a full run always decides both.
    """
    produced_module_page = any(
        getattr(page, "page_type", "") == "module_page" for page in (run.generated_pages or [])
    )
    has_code_to_group = any(
        not getattr(p.file_info, "is_test", False) for p in (run.parsed_files or [])
    )
    if produced_module_page or not has_code_to_group:
        run.authoritative_page_types.add("module_page")
    run.authoritative_page_types.add("layer_page")
    run.authoritative_page_types.add("scc_page")


async def _finalize_kg_enrichment(run: PipelineRun, kg_structural_task: asyncio.Task) -> None:
    """Join the structural enrichment and apply the page-derived summary backfill.

    A KG failure logs and leaves the skeleton in place; it never aborts the run.
    """
    from repowise.core.generation.knowledge_graph import finalize_knowledge_graph

    progress = run.progress
    if progress:
        progress.on_phase_start("knowledge_graph.enrich", None)
    try:
        enriched_layers, tour = await kg_structural_task
        run.knowledge_graph_result = finalize_knowledge_graph(
            kg_skeleton=run.knowledge_graph_result,
            enriched_layers=enriched_layers,
            tour=tour,
            generated_pages=run.generated_pages,
        )
        if progress:
            progress.on_message(
                "info",
                f"  ↳ KG enriched: {len(run.knowledge_graph_result.layers)} layers, "
                f"{len(run.knowledge_graph_result.tour)} tour steps",
            )
        _phase_done(progress, "knowledge_graph.enrich")
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        logger.error("knowledge_graph_enrichment_failed", error=str(exc), exc_info=True)


async def trace_execution_flows(run: PipelineRun) -> None:
    """Trace execution flows from entry points; failure only warns."""
    progress = run.progress
    if progress:
        progress.on_phase_start("graph.flows", None)
    try:
        run.execution_flow_report = await asyncio.to_thread(run.graph_builder.execution_flows)
    except Exception as _flow_err:
        logger.warning("execution_flow_tracing_skipped", error=str(_flow_err))
        emit_warning(
            progress,
            f"Execution flow tracing skipped ({_flow_err}); "
            "entry-point scores and flow-based pages will be absent.",
        )
    _phase_done(progress, "graph.flows")
