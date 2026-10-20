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
from repowise.core.pipeline.progress import ProgressCallback, emit_item, emit_phase_failed

from ._common import TEST_RUN_FILE_LIMIT, _phase_done, limit_to_top_pagerank

logger = structlog.get_logger(__name__)

# Page types rendered from a Jinja template with no provider call. Reused from
# the cost estimator rather than restated: that set is already the answer to
# "does this page cost tokens", and a second copy here would be the thing that
# drifts the first time a page type changes tier.
_FREE_PAGE_TYPES = STRUCTURAL_PAGE_TYPES


class _GenerationDisplay:
    """What generation shows beyond its page bars: the page it is on, and embedding.

    Embedding is its own step on screen, but only when the vectors carry
    signal: the keyless embedder's would read as semantic search that is off.
    """

    def __init__(self, progress: Any, vector_store: Any, on_page_ready: Any | None) -> None:
        from repowise.core.providers.embedding import store_has_semantic_vectors

        self._progress = progress
        self._on_page_ready = on_page_ready
        self._show_embedding = progress is not None and store_has_semantic_vectors(vector_store)
        self._embedded = 0
        # Batches started minus batches finished: a failed batch never finishes.
        self._open = 0
        self._started = False

    def on_embed(self, count: int, finished: bool) -> None:
        if not self._show_embedding:
            return
        if not finished:
            self._started = True
            self._open += 1
            self._progress.on_phase_start("embedding", self._embedded + count)
            return
        self._open -= 1
        self._embedded += count
        for _ in range(count):
            self._progress.on_item_done("embedding")

    def page_ready(self, page: Any) -> None:
        if self._progress is not None:
            phase = "generation" if page.page_type in _FREE_PAGE_TYPES else "generation.llm"
            emit_item(self._progress, phase, page.target_path)
        if self._on_page_ready is not None:
            self._on_page_ready(page)

    def close_embedding(self) -> None:
        if not self._started:
            return
        if self._open == 0:
            _phase_done(self._progress, "embedding")
        else:
            emit_phase_failed(self._progress, "embedding")


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
    selection_out: dict[str, Any] | None = None,
    persisted_page_ids: set[str] | None = None,
    stats_out: dict[str, int] | None = None,
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

    ``persisted_page_ids`` (ids with a stored page row) narrows what a
    ``resume`` run treats as already done; see ``PageGenerator.generate_all``.

    ``stats_out`` is filled with ``embed_failed_pages``: how many pages this
    run failed to embed, so a caller can refuse to call semantic search
    healthy after a failed write.

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
    assembler = ContextAssembler(config, repo_path=repo_path)

    # Test-run: limit to top 10 files by PageRank for a fast validation run.
    # Applied here rather than only in the orchestrator so it works whether the
    # pipeline ran with generate_docs=True or generation happened in a later,
    # separate phase (init's generate_docs=False flow) — the flag's documented
    # purpose is to cap the *generation* work, and this is where that happens.
    if test_run:
        parsed_files = limit_to_top_pagerank(parsed_files, graph_builder, n=TEST_RUN_FILE_LIMIT)
        if progress:
            progress.on_message("notice", f"Test run: limiting to {len(parsed_files)} files")

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

    def on_warning(text: str) -> None:
        """Surface a generation-time degradation through the progress callback
        so it lands in the run's warnings (persisted as ``degraded``), not
        only in structlog — the CLI pins structlog to ERROR unless -v, so a
        log-only warning is invisible on the path that matters (issue #1369)."""
        if progress:
            progress.on_message("warning", text)

    display = _GenerationDisplay(progress, vector_store, on_page_ready)

    generator = PageGenerator(
        llm_client,
        assembler,
        config,
        vector_store=vector_store,
        language=config.language,
        prior_pages=prior_pages or {},
        repo_path=repo_path,
    )

    # The recorder carries the run's shared totals table; generation's own
    # sub-spans write into it so they sit beside the top-level phases rather
    # than in a second table nothing reads.
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
        on_page_ready=display.page_ready,
        kg_modules=kg_modules,
        kg_data=kg_data,
        only_page_ids=only_page_ids,
        preserved_page_ids=preserved_page_ids,
        timings=getattr(progress, "table", None),
        on_warning=on_warning,
        persisted_page_ids=persisted_page_ids,
        on_embed=display.on_embed,
    )
    if stats_out is not None:
        stats_out["embed_failed_pages"] = generator.embed_failed_pages
    selection = getattr(generator, "selection", None)
    if selection_out is not None and selection is not None:
        selection_out.update(
            eligible=selection.eligible_file_pages,
            effective_cap=selection.effective_file_page_cap,
            # A resumed run may reuse already-persisted file pages rather than
            # returning them in ``generated_pages``.  Selection is the durable
            # contract: after a successful phase every selected file has a page.
            generated=len(selection.file_page_paths),
        )
        selection_out["omitted"] = max(0, selection_out["eligible"] - selection_out["generated"])

    # Which onboarding slots were made goes to the log; the screen has the
    # onboarding row's page count.
    onboarding_generated = [p for p in generated_pages if p.page_type == "onboarding"]
    promoted_present = {
        p.metadata.get("onboarding_slot")
        for p in generated_pages
        if p.metadata.get("onboarding_slot")
        and p.page_type in ("repo_overview", "architecture_diagram")
    }
    slots_made = sorted(
        {p.metadata.get("subkind", "?") for p in onboarding_generated} | promoted_present
    )
    logger.info(
        "generation_done",
        pages=len(generated_pages) - count_stub_fallbacks(generated_pages),
        onboarding_slots=slots_made,
    )
    _phase_done(progress, "onboarding")
    _phase_done(progress, "generation.llm")
    _phase_done(progress, "generation")
    display.close_embedding()

    return generated_pages
