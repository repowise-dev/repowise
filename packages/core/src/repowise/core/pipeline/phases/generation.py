"""Pipeline generation phase.

Extracted from the former monolithic ``orchestrator.py``; ``run_pipeline`` (in
orchestrator.py) imports these phase functions. No CLI/click/rich imports.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import structlog

from repowise.core.cost_estimator.estimator import STRUCTURAL_PAGE_TYPES
from repowise.core.generation.models import count_stub_fallbacks
from repowise.core.pipeline.progress import ProgressCallback

from ._common import TEST_RUN_FILE_LIMIT, _phase_done, limit_to_top_pagerank

logger = structlog.get_logger(__name__)

# Page types rendered from a Jinja template with no provider call. Reused from
# the cost estimator rather than restated: that set is already the answer to
# "does this page cost tokens", and a second copy here would be the thing that
# drifts the first time a page type changes tier.
_FREE_PAGE_TYPES = STRUCTURAL_PAGE_TYPES


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
    only_page_ids: set[str] | None = None,
    preserved_page_ids: set[str] | None = None,
    test_run: bool = False,
) -> list[Any]:
    """Run LLM-powered page generation.

    Returns a list of ``GeneratedPage`` objects.

    ``prior_pages`` (a ``page_id → PriorPage`` map loaded from a previous run)
    lets the generator skip the LLM call for any page whose freshly rendered
    prompt still hashes to the persisted value under the same model — the same
    cross-run reuse ``repowise update`` relies on. Defaults to empty.

    ``only_page_ids`` scopes the run to an explicit set of page ids (the
    ``repowise generate`` path). None means the full selection, as before.

    ``preserved_page_ids`` is an out-parameter filled by a ``resume`` run with
    the ids it skipped because a prior run already wrote them. Persistence
    needs it to keep those pages out of the stale sweep.

    ``test_run`` limits generation to the top 10 files by PageRank, so a quick
    validation run can exercise the whole generation path without paying for a
    full index's worth of pages.
    """
    from repowise.core.generation import (
        ContextAssembler,
        JobSystem,
        PageGenerator,
    )
    from repowise.core.persistence.vector_store import InMemoryVectorStore
    from repowise.core.providers.embedding.base import KeylessEmbedder

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

    # Test-run: limit to top 10 files by PageRank for a fast validation run.
    # Applied here rather than only in the orchestrator so it works whether the
    # pipeline ran with generate_docs=True or generation happened in a later,
    # separate phase (init's generate_docs=False flow) — the flag's documented
    # purpose is to cap the *generation* work, and this is where that happens.
    if test_run:
        parsed_files = limit_to_top_pagerank(
            parsed_files, graph_builder, n=TEST_RUN_FILE_LIMIT
        )
        if progress:
            progress.on_message("warning", f"Test run: limiting to {len(parsed_files)} files")

    # Resolve embedder and vector store
    embedder_impl = embedder if embedder is not None else KeylessEmbedder()

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
    _llm_pages_done = 0

    def on_page_done(page_type: str) -> None:
        nonlocal _pages_done, _llm_pages_done
        _pages_done += 1
        if not progress:
            return
        # Onboarding keeps its own named step as well as advancing its tier's
        # bar, so it stays visible as a distinct step in the terminal.
        if page_type == "onboarding":
            progress.on_item_done("onboarding")

        # Each page advances the bar for its own tier. One bar counting 4,229
        # items where ~4,134 are free template renders and ~95 are paid model
        # calls reads as frozen: the cheap levels run first, so it sprints to
        # 97% and then crawls for another quarter of an hour. Split, the free
        # bar finishes and hides while the paid one counts the stretch that is
        # actually still running.
        #
        # Routed by page *type*, not by whether a call was made, so a resume or
        # update run that reuses a cached page still counts it against the paid
        # tier. That matches how ``_announce_total`` derives the denominators,
        # which is what keeps the bar reaching 100%.
        if page_type in _FREE_PAGE_TYPES:
            progress.on_item_done("generation")
        else:
            _llm_pages_done += 1
            progress.on_item_done("generation.llm")

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
        only_page_ids=only_page_ids,
        preserved_page_ids=preserved_page_ids,
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
            # Imported here, not at module scope: importing the onboarding
            # package registers every subkind, and this module is on the CLI's
            # import path where that cost buys nothing.
            from repowise.core.generation.onboarding.slots import ONBOARDING_ORDER

            slots_made = sorted(
                {p.metadata.get("subkind", "?") for p in onboarding_generated} | promoted_present
            )
            progress.on_message(
                "info",
                # Derived rather than written down: the denominator was a
                # literal 8 and went stale the first time a slot was added.
                f"Onboarding: {len(slots_made)}/{len(ONBOARDING_ORDER)} slots"
                f" — {', '.join(slots_made)}",
            )
        # Placeholders left behind by a failed provider call are in this list
        # like any other page, and the run reports them as failures a line
        # later. Counting the list here made the two lines disagree.
        progress.on_message(
            "info",
            f"Generated {len(generated_pages) - count_stub_fallbacks(generated_pages)} pages",
        )
        # Surface the FAQ-weighted budget tilt when session demand shaped it
        # (silent on fresh repos with no history — nothing to weight yet).
    _phase_done(progress, "onboarding")
    _phase_done(progress, "generation.llm")
    _phase_done(progress, "generation")

    return generated_pages
