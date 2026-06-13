"""Pipeline generation phase.

Extracted from the former monolithic ``orchestrator.py``; ``run_pipeline`` (in
orchestrator.py) imports these phase functions. No CLI/click/rich imports.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import structlog

from repowise.core.pipeline.progress import ProgressCallback

from ._common import _phase_done

logger = structlog.get_logger(__name__)


async def run_generation(
    *,
    repo_path: Path,
    parsed_files: list[Any],
    source_map: dict[str, bytes],
    graph_builder: Any,
    repo_structure: Any,
    git_meta_map: dict[str, dict],
    llm_client: Any,
    embedder: Any | None,
    vector_store: Any | None,
    concurrency: int,
    progress: ProgressCallback | None,
    resume: bool = False,
    cost_tracker: Any | None = None,
    generation_config: Any | None = None,
    dead_code_report: Any | None = None,
    decision_report: Any | None = None,
    external_systems: list[dict] | None = None,
    on_page_ready: Any | None = None,
    prior_pages: dict[str, Any] | None = None,
    kg_modules: list[dict] | None = None,
    kg_data: dict | None = None,
) -> list[Any]:
    """Run LLM-powered page generation.

    Returns a list of ``GeneratedPage`` objects.

    ``prior_pages`` (a ``page_id → PriorPage`` map loaded from a previous run)
    lets the generator skip the LLM call for any page whose freshly rendered
    prompt still hashes to the persisted value under the same model — the same
    cross-run reuse ``repowise update`` relies on. Defaults to empty.
    """
    from repowise.core.generation import (
        ContextAssembler,
        JobSystem,
        PageGenerator,
    )
    from repowise.core.persistence.vector_store import InMemoryVectorStore
    from repowise.core.providers.embedding.base import MockEmbedder

    # Attach cost tracker to LLM client if available
    if cost_tracker is not None and llm_client is not None and hasattr(llm_client, "_cost_tracker"):
        llm_client._cost_tracker = cost_tracker

    from repowise.core.generation import GenerationConfig

    # Preserve all caller-supplied GenerationConfig fields (output language, cache flags,
    # token budgets, etc.) and only override max_concurrency to match the resolved value.
    # Falls back to defaults when the pipeline entry point did not thread one through.
    base_config = generation_config if generation_config is not None else GenerationConfig()
    config = replace(base_config, max_concurrency=concurrency)
    assembler = ContextAssembler(config)

    # Resolve embedder and vector store
    embedder_impl = embedder if embedder is not None else MockEmbedder()

    if vector_store is None:
        vector_store = InMemoryVectorStore(embedder_impl)

    # Job system — use a temp-like dir under repo_path for checkpoints
    jobs_dir = repo_path / ".repowise" / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job_system = JobSystem(jobs_dir)

    repo_name = repo_path.name

    # Track generation progress. Onboarding pages get routed to their own
    # phase so the terminal UI shows them as a distinct, named step rather
    # than blending into the long file_page run.
    _pages_done = 0

    def on_page_done(page_type: str) -> None:
        nonlocal _pages_done
        _pages_done += 1
        if progress:
            phase = "onboarding" if page_type == "onboarding" else "generation"
            progress.on_item_done(phase)
            # Push live cost update if the callback supports it
            if cost_tracker is not None and hasattr(progress, "set_cost"):
                progress.set_cost(cost_tracker.session_cost)

    if progress:
        progress.on_phase_start("generation", None)

    def on_total_known(total: int) -> None:
        if progress:
            progress.on_phase_start("generation", total)

    def on_subphase(name: str, total: int | None) -> None:
        """Start a distinct sub-phase (currently used only for onboarding)."""
        if progress:
            progress.on_phase_start(name, total)

    generator = PageGenerator(
        llm_client,
        assembler,
        config,
        vector_store=vector_store,
        language=config.language,
        prior_pages=prior_pages or {},
        repo_path=repo_path,
    )

    generated_pages = await generator.generate_all(
        parsed_files,
        source_map,
        graph_builder,
        repo_structure,
        repo_name,
        job_system=job_system,
        on_page_done=on_page_done,
        on_total_known=on_total_known,
        on_subphase=on_subphase,
        git_meta_map=git_meta_map if git_meta_map else None,
        resume=resume,
        repo_path=repo_path,
        dead_code_report=dead_code_report,
        decision_report=decision_report,
        external_systems=external_systems,
        on_page_ready=on_page_ready,
        kg_modules=kg_modules,
        kg_data=kg_data,
    )

    # Onboarding summary — count generated slots and surface which ones
    # were gated out so the user can see the curated collection's state.
    onboarding_generated = [p for p in generated_pages if p.page_type == "onboarding"]
    promoted_present = {
        p.metadata.get("onboarding_slot")
        for p in generated_pages
        if p.metadata.get("onboarding_slot")
        and p.page_type in ("repo_overview", "architecture_diagram")
    }
    if progress:
        if onboarding_generated or promoted_present:
            slots_made = sorted(
                {p.metadata.get("subkind", "?") for p in onboarding_generated} | promoted_present
            )
            progress.on_message(
                "info",
                f"Onboarding: {len(slots_made)}/8 slots — {', '.join(slots_made)}",
            )
        progress.on_message("info", f"Generated {len(generated_pages)} pages")
    _phase_done(progress, "onboarding")
    _phase_done(progress, "generation")

    return generated_pages
