"""Programmatic pipeline orchestrator for repowise.

Provides ``run_pipeline()`` — the single entry point for running the full
repowise indexing/analysis/generation pipeline without any CLI dependencies.

This module has **zero** imports from ``repowise.cli``, ``click``, or ``rich``.
All progress reporting is done through the optional ``ProgressCallback`` protocol.

Callers:
    - CLI (``init_cmd.py``) — passes a Rich-backed ProgressCallback, persists to SQLite
    - Modal worker (Phase 2) — passes LoggingProgressCallback, serializes to files
    - Tests — passes None, inspects PipelineResult in memory
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from repowise.core.ingestion.git_indexer._constants import _DEFAULT_COMMIT_LIMIT
from repowise.core.pipeline.modes import OrchestratorMode
from repowise.core.pipeline.progress import ProgressCallback, emit_warning

from ._run.analysis import run_analysis_phase
from ._run.index import run_index_phase
from ._run.state import PipelineRun, attach_cost_tracker, hooked_progress, start_checkpointer
from .phases._common import _phase_done

# The ``_run`` stage modules call these phase functions through this module
# (``_orch.<name>``), so imports and patches on the orchestrator keep reaching them.
# ruff: noqa: F401
from .phases.analysis import (
    _run_dead_code_analysis,
    _run_decision_extraction,
    _run_doc_drift_analysis,
    _run_health_analysis,
)
from .phases.generation import run_generation
from .phases.git import (
    _run_git_indexing,
    drop_transient_git_signals,
    label_co_change_structure,
)
from .phases.ingestion import _run_ingestion, reparse_for_resume
from .resume.controller import ResumeController

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """All outputs from a pipeline run, held in memory.

    The caller decides how to persist — SQLite, files for upload, or nothing.
    """

    # Ingestion
    parsed_files: list[Any]
    """List of ``ParsedFile`` objects from the AST parser."""

    file_infos: list[Any]
    """All traversed ``FileInfo`` objects (pre-filter)."""

    repo_structure: Any
    """``RepoStructure`` — monorepo detection result."""

    source_map: dict[str, bytes]
    """Mapping of relative file path → raw source bytes."""

    # Graph
    graph_builder: Any
    """``GraphBuilder`` instance — call ``.graph()``, ``.pagerank()``, etc."""

    # Git
    git_metadata_list: list[dict]
    """Raw metadata dicts ready for ``upsert_git_metadata_bulk``."""

    git_meta_map: dict[str, dict]
    """File path → git metadata dict."""

    git_summary: Any | None
    """``GitIndexSummary`` or None if git indexing was skipped."""

    # Analysis
    dead_code_report: Any | None
    """``DeadCodeReport`` or None."""

    decision_report: Any | None
    """``DecisionExtractionReport`` or None."""

    # Generation (None when generate_docs=False)
    generated_pages: list[Any] | None
    """List of ``GeneratedPage`` objects, or None if docs weren't generated."""

    # Stats
    repo_name: str
    file_count: int
    symbol_count: int
    languages: set[str] = field(default_factory=set)
    elapsed_seconds: float = 0.0

    execution_flow_report: Any | None = None
    """``ExecutionFlowReport`` or None (populated after graph build)."""

    health_report: Any | None = None
    """``HealthReport`` or None — populated by ``_run_health_analysis``."""

    doc_drift_report: Any | None = None
    """``DocDriftReport`` or None — populated by ``_run_doc_drift_analysis``."""

    # Traversal stats
    traversal_stats: Any | None = None
    """``TraversalStats`` from the file traverser, or None."""

    # Detected tech stack (languages, frameworks, databases, infra).
    # Stored as plain dicts (``{"name", "version", "category"}``) so the
    # persistence layer can serialise without importing the editor_files
    # data module. Populated post-traversal during the graph build phase.
    tech_stack: list[dict] = field(default_factory=list)

    repo_path: str = ""
    """Working tree this run walked. Empty when the caller did not set it;
    persistence steps that need the tree (the per-commit health scan) skip."""

    # External systems parsed from repo manifests (package.json,
    # pyproject.toml, Cargo.toml, go.mod, .csproj). Powers the C4 L1
    # System Context view. Plain dicts mirroring ExternalSystemRecord fields
    # for the same reason as tech_stack.
    external_systems: list[dict] = field(default_factory=list)

    knowledge_graph_result: Any | None = None
    """``KnowledgeGraphResult`` or None — populated after community detection."""

    vector_store: Any | None = None
    """The shared page-generator vector store used this run, threaded to
    persistence so the decision semantic-dedup pass (Phase 2C) can match
    against it and embed decisions into it (also surfacing them in
    search_codebase). None when no store was configured (semantic dedup is
    then skipped; title dedup still runs)."""

    index_persisted_incrementally: bool = False
    """True when a ResumeController persisted (or rehydrated) the INDEX phase
    during the run. The caller's final persist then skips the index portion —
    it is already on disk — and writes only analysis + generation. False for
    every non-resume caller, which persist the full result as before."""

    authoritative_page_types: set[str] = field(default_factory=set)
    """Structural page types this run fully decided — i.e. its emitted set is
    "exactly these, possibly none". The stale-page sweep retires prior rows of
    a type when it was produced OR is named here, so a legitimately-empty
    authoritative type (e.g. every curated module collapsed into its layer via
    wholeLayer) still wipes the previous run's stragglers. Populated only on a
    curated run with a real KG; left empty on degraded/community fallback and
    on incremental no-KG paths, which preserves degradation honesty (a fallback
    run never wipes curated pages it could not reproduce)."""

    preserved_page_ids: set[str] = field(default_factory=set)
    """Page ids a ``--resume`` run skipped because a prior run already wrote
    them. They are absent from ``generated_pages`` by design, so the stale-page
    sweep would read them as gone and delete the very pages the resume existed
    to keep (issue #1089). Empty on every non-resume run.

    Filled here for symmetry, but the live route is the other one: ``--resume``
    only exists on ``init``, which calls ``run_pipeline(generate_docs=False)``
    and generates separately, so the init flows set this on the result
    themselves the same way they set ``generated_pages``. No caller currently
    reaches the assignment below (it needs ``resume`` and ``generate_docs``
    together), so treat this field's value on a ``run_pipeline`` result as
    untested until one does."""


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


async def run_pipeline(
    repo_path: Path | str,
    *,
    commit_depth: int = _DEFAULT_COMMIT_LIMIT,
    follow_renames: bool = False,
    skip_tests: bool = False,
    skip_infra: bool = False,
    exclude_patterns: list[str] | None = None,
    include_submodules: bool = False,
    include_nested_repos: bool = False,
    generate_docs: bool = False,
    llm_client: Any | None = None,
    embedder: Any | None = None,
    vector_store: Any | None = None,
    concurrency: int = 5,
    test_run: bool = False,
    resume: bool = False,
    mode: OrchestratorMode = OrchestratorMode.STANDARD,
    job_store: Any | None = None,
    progress: ProgressCallback | None = None,
    cost_tracker: Any | None = None,
    generation_config: Any | None = None,
    wiki_style: str | None = None,
    existing_kg_fingerprint: str | None = None,
    on_page_ready: Any | None = None,
    resume_controller: ResumeController | None = None,
    coverage_report_paths: list[Path] | None = None,
    derive_environment_facts: bool = False,
) -> PipelineResult:
    """Run the repowise indexing/analysis/generation pipeline.

    Parameters
    ----------
    repo_path:
        Path to an already-cloned repository on disk.
    commit_depth:
        Maximum commits to analyse per file (1-10000). Default 5000.
    follow_renames:
        Use ``git log --follow`` to track files across renames.
    skip_tests:
        Exclude test files from parsing.
    skip_infra:
        Exclude infrastructure files (Dockerfile, Makefile, etc.) from parsing.
    exclude_patterns:
        Additional gitignore-style exclusion patterns.
    generate_docs:
        When True, run LLM page generation (requires *llm_client*).
    llm_client:
        A configured ``BaseProvider`` instance for LLM calls. Required when
        *generate_docs* is True, optional otherwise (used for decision extraction).
    embedder:
        A configured embedder instance for vector embeddings. Falls back to
        ``MockEmbedder`` when None.
    vector_store:
        A pre-constructed vector store (e.g. ``LanceDBVectorStore``). Falls
        back to ``InMemoryVectorStore`` when None and *generate_docs* is True.
    concurrency:
        Maximum concurrent LLM calls during generation.
    test_run:
        Limit generation to top 10 files by PageRank (for quick validation).
    progress:
        Optional callback for progress reporting. Pass None for silent operation.
    generation_config:
        Optional ``GenerationConfig`` used for page generation. When omitted,
        generation uses repo-local config such as ``reasoning`` from
        ``.repowise/config.yaml`` and ``REPOWISE_REASONING``.

    derive_environment_facts:
        Derive the structural facts that describe the *machine* this index runs
        on (currently whether the tree is formatter-clean, which costs one
        subprocess). Off by default and set only by the local ``init`` command:
        a hosted or CI indexer would otherwise measure its own container and
        store the answer as a fact about the user's repository. The facts read
        off the walk itself are unconditional and unaffected by this flag.

    Returns
    -------
    PipelineResult
        All pipeline outputs held in memory.
    """
    repo_path = Path(repo_path).resolve()
    start = time.monotonic()
    progress = hooked_progress(progress)
    checkpointer = await start_checkpointer(job_store, repo_path)
    attach_cost_tracker(llm_client, cost_tracker)
    run = PipelineRun(
        repo_path=repo_path,
        start=start,
        mode=mode,
        # Mode policy, the single switch point: FAST forces ESSENTIAL git indexing and
        # disables doc generation (so every LLM call); DETERMINISTIC renders pages from
        # templates; STANDARD keeps the caller's flags. Later stages read these, not mode.
        generate_docs=generate_docs and mode.allows_doc_generation,
        git_tier=mode.git_tier,
        progress=progress,
        checkpointer=checkpointer,
        commit_depth=max(1, min(commit_depth, 10000)),
        follow_renames=follow_renames,
        skip_tests=skip_tests,
        skip_infra=skip_infra,
        exclude_patterns=exclude_patterns,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
        derive_environment_facts=derive_environment_facts,
        llm_client=llm_client,
        embedder=embedder,
        vector_store=vector_store,
        concurrency=concurrency,
        test_run=test_run,
        resume=resume,
        generation_config=generation_config,
        wiki_style=wiki_style,
        existing_kg_fingerprint=existing_kg_fingerprint,
        on_page_ready=on_page_ready,
        resume_controller=resume_controller,
        coverage_report_paths=coverage_report_paths,
    )
    generate_docs = run.generate_docs

    await run_index_phase(run)
    parsed_files = run.parsed_files
    file_infos = run.file_infos
    repo_structure = run.repo_structure
    source_map = run.source_map
    graph_builder = run.graph_builder
    traversal_stats = run.traversal_stats
    tech_items = run.tech_items
    git_summary = run.git_summary
    git_metadata_list = run.git_metadata_list
    git_meta_map = run.git_meta_map
    external_systems = run.external_systems

    await run_analysis_phase(run)
    parsed_files = run.parsed_files
    dead_code_report = run.dead_code_report
    health_report = run.health_report
    decision_report = run.decision_report
    doc_drift_report = run.doc_drift_report
    gen_dead_code_report = run.gen_dead_code_report
    gen_decision_report = run.gen_decision_report
    tech_stack_dicts = run.tech_stack_dicts
    knowledge_graph_result = run.knowledge_graph_result

    # ---- Phase 3: Generation (optional) ------------------------------------
    generated_pages: list[Any] | None = None
    # Structural page types this run was authoritative for (see
    # PipelineResult.authoritative_page_types). Stays empty unless generation
    # runs below, because a run that generated nothing decided nothing.
    authoritative_page_types: set[str] = set()
    # Filled by generation when resuming; see PipelineResult.preserved_page_ids.
    preserved_page_ids: set[str] = set()
    # DETERMINISTIC supplies its own null provider so a caller with no key can
    # still get a wiki. Scoped to this phase rather than assigned over
    # ``llm_client``: the analysis phase above shares that client, and its
    # decision extractor branches on truthiness, so a non-None provider that
    # raises on every call would cost it the heuristic fallbacks it would
    # otherwise have taken with no provider at all.
    generation_client = llm_client
    if generate_docs and mode.deterministic_docs and generation_client is None:
        from repowise.core.providers.llm.template import TemplateProvider

        generation_client = TemplateProvider()

    if generate_docs and generation_client is not None:
        if progress:
            progress.on_message("info", "Phase 3: Generation")

        resolved_generation_config = generation_config
        if resolved_generation_config is None:
            from repowise.core.generation import GenerationConfig
            from repowise.core.reasoning import resolve_reasoning
            from repowise.core.repo_config import load_repo_config

            _cfg = load_repo_config(repo_path)
            # Wiki style precedence: explicit param (server passes the DB-settings
            # value) > repo-local config.yaml (CLI/init) > default.
            _style = wiki_style or _cfg.get("wiki_style", "comprehensive")
            resolved_generation_config = GenerationConfig.from_repo_config(
                _cfg,
                max_concurrency=concurrency,
                reasoning=resolve_reasoning(config=_cfg),
                wiki_style=_style,
                language=_cfg.get("language", "en"),
            )

        # The mode decides how pages are rendered, not the caller's config.
        # A DETERMINISTIC run with an LLM-shaped config would still prompt.
        if mode.deterministic_docs and not resolved_generation_config.deterministic:
            from dataclasses import replace as _dc_replace

            resolved_generation_config = _dc_replace(resolved_generation_config, deterministic=True)

        # Phase 2 enrichment: flag framework-defined HTTP surfaces (FastAPI,
        # ASP.NET controllers, …) as api_contract so they route through the
        # api_contract template instead of generic file_page. Lives in the
        # generation package so language-specific heuristics stay co-located
        # with the templates that consume them.
        from repowise.core.generation import detect_code_api_contracts as _detect_apis

        try:
            flipped = _detect_apis(parsed_files)
            if flipped and progress:
                progress.on_message("info", f"→ Detected {flipped} additional API contract file(s)")
        except Exception as _api_err:
            logger.warning("api_contract_detection_failed", error=str(_api_err))
            emit_warning(
                progress,
                f"API contract detection skipped ({_api_err}); endpoint pages may be missing.",
            )

        # Launch the page-independent half of KG enrichment (LLM layer naming +
        # tour) so it overlaps page generation instead of running strictly
        # after it. The two do not read each other's live output: run_generation
        # receives a to_dict() snapshot of the skeleton (kg_data/kg_modules
        # below), and structural enrichment reads only the skeleton layers plus
        # the warmed graph metrics. Layer/tour results are applied to the
        # skeleton only in finalize_knowledge_graph (after generation), so the
        # in-place layer-name mutation inside enrichment can never reach the
        # snapshot generation is already holding. The page-dependent finalize
        # (summary backfill) runs after both complete.
        kg_structural_task = None
        # Skipped in DETERMINISTIC mode: layer naming and the tour are pure
        # prompting, and the null provider raises rather than stubbing. The
        # skeleton's structural layers stand on their own.
        if knowledge_graph_result is not None and not mode.deterministic_docs:
            from repowise.core.generation.knowledge_graph import (
                enrich_knowledge_graph_structural,
            )

            _kg_reasoning = (
                getattr(resolved_generation_config, "reasoning", "auto")
                if resolved_generation_config
                else "auto"
            )
            # Warm the pagerank cache on this thread before the concurrent
            # launch so both the structural task and generation only ever read
            # it. It is already populated during ingestion; this makes the
            # no-race invariant explicit rather than incidental.
            graph_builder.pagerank()
            kg_structural_task = asyncio.create_task(
                enrich_knowledge_graph_structural(
                    kg_skeleton=knowledge_graph_result,
                    llm_client=llm_client,
                    graph_builder=graph_builder,
                    repo_structure=repo_structure,
                    tech_stack=tech_stack_dicts,
                    reasoning=_kg_reasoning,
                )
            )

        try:
            generated_pages = await run_generation(
                repo_path=repo_path,
                parsed_files=parsed_files,
                source_map=source_map,
                graph_builder=graph_builder,
                repo_structure=repo_structure,
                git_meta_map=git_meta_map,
                llm_client=generation_client,
                embedder=embedder,
                vector_store=vector_store,
                concurrency=concurrency,
                progress=progress,
                resume=resume,
                generation_config=resolved_generation_config,
                dead_code_report=gen_dead_code_report,
                decision_report=gen_decision_report,
                external_systems=external_systems,
                on_page_ready=on_page_ready,
                preserved_page_ids=preserved_page_ids,
                # In-memory KG — the artifact file is written after generation,
                # so it cannot carry layers/tour/modules on a fresh init.
                kg_modules=(
                    knowledge_graph_result.modules or None
                    if knowledge_graph_result is not None
                    else None
                ),
                kg_data=(
                    knowledge_graph_result.to_dict() if knowledge_graph_result is not None else None
                ),
            )
        except BaseException:
            # Generation aborted the run: cancel the concurrent enrichment task
            # so it stops issuing LLM calls and never surfaces as a stray
            # "Task exception was never retrieved" during teardown.
            if kg_structural_task is not None:
                kg_structural_task.cancel()
                with contextlib.suppress(BaseException):
                    await kg_structural_task
            raise

        # Record which structural page types this run authoritatively decided,
        # so the sweep can retire prior rows of a type even when this run
        # legitimately emitted zero pages of it.
        #
        # ``module_page`` needs no KG and no budget: the concept grouping
        # partitions every production file, so a run that produced any concept
        # page produced all of them and prior rows should go. The condition
        # that used to stand here tested whether a curated KG artifact had
        # engaged a grouping mode that no longer exists, which stranded the
        # superseded pages on exactly the repositories the KG failed on.
        #
        # It is still not unconditional, because "emitted zero" has causes
        # other than "there is nothing to document": ``file_pages_only``
        # returns before the concept level runs at all, and a provider error
        # on that level is logged per page rather than raised. Claiming
        # authority there would delete every concept page over a 429. So the
        # claim is made on evidence — at least one page produced — with the
        # genuinely empty repository as the one exception, since it has no
        # page to show and its stale rows are exactly what wants sweeping.
        produced_module_page = any(
            getattr(page, "page_type", "") == "module_page" for page in (generated_pages or [])
        )
        has_code_to_group = any(
            not getattr(p.file_info, "is_test", False) for p in (parsed_files or [])
        )
        if produced_module_page or not has_code_to_group:
            authoritative_page_types.add("module_page")
        # Layer pages are retired: no run emits one and no failure mode can
        # make a run emit one, so every completed run is authoritative for the
        # type and the sweep retires the rows a pre-retirement index left
        # behind. Unconditional, unlike the types above, because "emitted
        # zero" here has exactly one cause. Their ids keep resolving through
        # the redirect table, so retiring the rows breaks no inbound link.
        authoritative_page_types.add("layer_page")
        # An SCC page missing from a full run's output means the cycle is gone,
        # so the run is authoritative for the type and the sweep may clear the
        # pages of cycles that no longer exist. This used to be claimed only for
        # a deterministic run, on the premise that a budgeted run's zero might
        # mean "the allocation was zero" rather than "there are no cycles" —
        # but selection applies no budget to scc_groups (selection/selector.py
        # _build_scc_candidates, unlike the file-page path), so zero produced
        # really does mean zero cycles on every full run. Holding the narrower
        # rule left a keyed index of a repo whose cycles all disappeared unable
        # to ever retire them.
        authoritative_page_types.add("scc_page")

        # ---- Knowledge Graph enrichment: join + finalize ----------------------
        # The structural half (layer naming + tour) ran concurrently with
        # generation above; join it now and apply the page-derived summary
        # backfill. Same try/except envelope as before: a KG failure logs and
        # leaves the skeleton in place, never aborting the run.
        if kg_structural_task is not None:
            from repowise.core.generation.knowledge_graph import finalize_knowledge_graph

            if progress:
                progress.on_phase_start("knowledge_graph.enrich", None)
            try:
                enriched_layers, tour = await kg_structural_task
                knowledge_graph_result = finalize_knowledge_graph(
                    kg_skeleton=knowledge_graph_result,
                    enriched_layers=enriched_layers,
                    tour=tour,
                    generated_pages=generated_pages,
                )
                if progress:
                    progress.on_message(
                        "info",
                        f"  ↳ KG enriched: {len(knowledge_graph_result.layers)} layers, "
                        f"{len(knowledge_graph_result.tour)} tour steps",
                    )
                _phase_done(progress, "knowledge_graph.enrich")
            except (ValueError, KeyError, OSError, RuntimeError) as exc:
                logger.error("knowledge_graph_enrichment_failed", error=str(exc), exc_info=True)

    # ---- Execution flow tracing -----------------------------------------------
    execution_flow_report = None
    if progress:
        progress.on_phase_start("graph.flows", None)
    try:
        execution_flow_report = await asyncio.to_thread(graph_builder.execution_flows)
    except Exception as _flow_err:
        logger.warning("execution_flow_tracing_skipped", error=str(_flow_err))
        emit_warning(
            progress,
            f"Execution flow tracing skipped ({_flow_err}); "
            "entry-point scores and flow-based pages will be absent.",
        )
    _phase_done(progress, "graph.flows")

    # ---- Build result -------------------------------------------------------
    # Flush checkpoints on the success path; on an exception above this point
    # the checkpointer is intentionally not closed so interrupted phases stay
    # RUNNING for a resumable re-run.
    if checkpointer is not None:
        await checkpointer.aclose()

    elapsed = time.monotonic() - start
    languages = {fi.language for fi in file_infos if hasattr(fi, "language") and fi.language}
    symbol_count = sum(len(pf.symbols) for pf in parsed_files)

    return PipelineResult(
        repo_path=str(repo_path),
        parsed_files=parsed_files,
        file_infos=file_infos,
        repo_structure=repo_structure,
        source_map=source_map,
        graph_builder=graph_builder,
        git_metadata_list=git_metadata_list,
        git_meta_map=git_meta_map,
        git_summary=git_summary,
        dead_code_report=dead_code_report,
        decision_report=decision_report,
        health_report=health_report,
        doc_drift_report=doc_drift_report,
        execution_flow_report=execution_flow_report,
        knowledge_graph_result=knowledge_graph_result,
        generated_pages=generated_pages,
        traversal_stats=traversal_stats,
        repo_name=repo_path.name,
        file_count=len(parsed_files),
        symbol_count=symbol_count,
        languages=languages,
        elapsed_seconds=elapsed,
        tech_stack=[
            {"name": t.name, "version": t.version, "category": t.category} for t in tech_items
        ],
        external_systems=external_systems,
        vector_store=vector_store,
        index_persisted_incrementally=(
            resume_controller.index_persisted if resume_controller is not None else False
        ),
        authoritative_page_types=authoritative_page_types,
        preserved_page_ids=preserved_page_ids,
    )


# ---------------------------------------------------------------------------
# Phase helpers (private)
# ---------------------------------------------------------------------------
