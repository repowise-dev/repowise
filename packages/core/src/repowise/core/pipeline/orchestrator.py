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

from repowise.core.pipeline.modes import OrchestratorMode
from repowise.core.pipeline.progress import ProgressCallback
from repowise.core.registry import HookProgressCallback

from .phases._common import _phase_done
from .phases.analysis import (
    _run_dead_code_analysis,
    _run_decision_extraction,
    _run_health_analysis,
)
from .phases.generation import run_generation
from .phases.git import _run_git_indexing, drop_transient_git_signals
from .phases.ingestion import _run_ingestion, reparse_for_resume
from .resume import ResumePhase
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

    # Traversal stats
    traversal_stats: Any | None = None
    """``TraversalStats`` from the file traverser, or None."""

    # Detected tech stack (languages, frameworks, databases, infra).
    # Stored as plain dicts (``{"name", "version", "category"}``) so the
    # persistence layer can serialise without importing the editor_files
    # data module. Populated post-traversal during the graph build phase.
    tech_stack: list[dict] = field(default_factory=list)

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


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


async def run_pipeline(
    repo_path: Path | str,
    *,
    commit_depth: int = 500,
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
) -> PipelineResult:
    """Run the repowise indexing/analysis/generation pipeline.

    Parameters
    ----------
    repo_path:
        Path to an already-cloned repository on disk.
    commit_depth:
        Maximum commits to analyse per file (1-10000). Default 500.
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

    Returns
    -------
    PipelineResult
        All pipeline outputs held in memory.
    """
    repo_path = Path(repo_path).resolve()
    start = time.monotonic()

    commit_depth = max(1, min(commit_depth, 10000))

    # Mode policy: FAST forces ESSENTIAL git indexing and disables doc
    # generation (and therefore all LLM calls). DETERMINISTIC generates every
    # page from a template and supplies its own null provider, so a caller
    # with no API key still gets a wiki. STANDARD preserves the caller's flags
    # exactly. This is the single switch point: the rest of the pipeline
    # reads ``generate_docs`` / the git tier, not ``mode``.
    git_tier = mode.git_tier
    generate_docs = generate_docs and mode.allows_doc_generation

    # Wrap the incoming progress callback so registered pipeline hooks fire
    # around each phase transition. Zero-op when no hooks are registered.
    progress = HookProgressCallback(progress)

    # Optional crash-resume checkpointing. When a JobStore is supplied, a
    # checkpointer records each major phase's lifecycle (via the same hook
    # seam) so a re-run can detect completed phases. No-op otherwise — the
    # default (None) preserves existing behaviour exactly.
    _checkpointer = None
    if job_store is not None:
        from repowise.core.pipeline.checkpoint import PhaseCheckpointer

        _checkpointer = PhaseCheckpointer(job_store, str(repo_path))
        await _checkpointer.start()

    # Attach cost tracker to provider if supplied
    if cost_tracker is not None and llm_client is not None and hasattr(llm_client, "_cost_tracker"):
        llm_client._cost_tracker = cost_tracker

    # ---- Phase 1: Ingestion ------------------------------------------------
    if progress:
        progress.on_message("info", "Phase 1: Ingestion")

    # Launch git indexing as a background task immediately — it is independent
    # of parsing and graph-build, so the two stages can run concurrently.
    # _run_ingestion does: traverse → ProcessPool parse → graph build → dynamic hints.
    # _run_git_indexing does: git log → co-change accumulation (I/O bound, own executor).

    async def _git_stage() -> tuple:
        return await _run_git_indexing(
            repo_path,
            commit_depth=commit_depth,
            follow_renames=follow_renames,
            tier=git_tier,
            exclude_patterns=exclude_patterns,
            progress=progress,
        )

    async def _ingestion_stage() -> tuple:
        return await _run_ingestion(
            repo_path,
            exclude_patterns=exclude_patterns,
            include_submodules=include_submodules,
            include_nested_repos=include_nested_repos,
            skip_tests=skip_tests,
            skip_infra=skip_infra,
            progress=progress,
        )

    # Resume fast-path: when a prior run already persisted the INDEX phase
    # (graph + git), rehydrate it from the DB and only re-parse source files —
    # skipping the git history walk and the graph centrality kernels, which
    # are the minutes-long work that makes a first index slow. Falls back to a
    # full compute if rehydration yields no graph (nothing was persisted).
    skip_index = bool(resume_controller and await resume_controller.can_skip(ResumePhase.INDEX))
    git_summary = None
    if skip_index:
        try:
            graph_builder, git_meta_map = await resume_controller.rehydrate_index(repo_path)
            if progress:
                progress.on_message("info", "  ↳ Resuming — reusing persisted graph + git index")
            (
                parsed_files,
                file_infos,
                repo_structure,
                source_map,
                tech_items,
            ) = await reparse_for_resume(
                repo_path,
                exclude_patterns=exclude_patterns,
                include_submodules=include_submodules,
                include_nested_repos=include_nested_repos,
                skip_tests=skip_tests,
                skip_infra=skip_infra,
                progress=progress,
            )
            traversal_stats = None
            git_metadata_list = list(git_meta_map.values())
        except Exception as exc:
            logger.warning("resume_rehydrate_failed_recomputing", error=str(exc))
            skip_index = False

    if not skip_index:
        (
            (
                parsed_files,
                file_infos,
                repo_structure,
                source_map,
                graph_builder,
                traversal_stats,
                tech_items,
            ),
            (
                git_summary,
                git_metadata_list,
                git_meta_map,
            ),
        ) = await asyncio.gather(_ingestion_stage(), _git_stage())

        # Add co-change edges to the graph (rehydrated graphs already carry them)
        if git_meta_map:
            graph_builder.add_co_change_edges(git_meta_map)

    # ---- External systems (C4 L1) ------------------------------------------
    # Parse repo manifests for declared third-party dependencies. Failure here
    # must not break the pipeline — log and continue with an empty list.
    external_systems: list[dict] = []
    if progress:
        progress.on_phase_start("external_systems", None)
    try:
        from repowise.core.ingestion.external_systems import extract_external_systems

        records = await asyncio.to_thread(extract_external_systems, repo_path)
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
    _phase_done(progress, "external_systems")

    # ---- Checkpoint: INDEX -------------------------------------------------
    # Persist the freshly-computed graph + git + symbols now so an interrupt
    # during the analysis phase below can resume without redoing the expensive
    # index. Skipped when we rehydrated (already persisted) — best-effort.
    if resume_controller is not None and not skip_index:
        await resume_controller.checkpoint_index(
            parsed_files=parsed_files,
            graph_builder=graph_builder,
            git_metadata_list=git_metadata_list,
            git_summary=git_summary,
            external_systems=external_systems,
            source_map=source_map,
        )

    # Emit rich insight summary for the ingestion phase
    if progress:
        _g = graph_builder.graph()
        _n_nodes = _g.number_of_nodes()
        _n_edges = _g.number_of_edges()
        progress.on_message(
            "info",
            f"→ {len(parsed_files):,} files parsed · "
            f"{sum(len(pf.symbols) for pf in parsed_files):,} symbols extracted",
        )
        progress.on_message(
            "info",
            f"→ Graph: {_n_nodes:,} nodes · {_n_edges:,} edges",
        )
        if git_summary and git_summary.files_indexed:
            _hotspot_msg = ""
            if hasattr(git_summary, "hotspots") and git_summary.hotspots:
                _hotspot_msg = f" · {git_summary.hotspots} hotspots"
            progress.on_message(
                "info",
                f"→ Git: {git_summary.files_indexed:,} files indexed{_hotspot_msg}",
            )

    # Test-run: limit to top 10 files by PageRank
    if test_run and generate_docs:
        try:
            import networkx as nx

            ranks = nx.pagerank(graph_builder.graph())
        except Exception:
            ranks = {}
        parsed_files = sorted(
            parsed_files,
            key=lambda pf: ranks.get(pf.file_info.path, 0),
            reverse=True,
        )[:10]
        if progress:
            progress.on_message("warning", f"Test run: limiting to {len(parsed_files)} files")

    # ---- Phase 2: Analysis --------------------------------------------------
    if progress:
        progress.on_message("info", "Phase 2: Analysis")

    # Resume fast-path: when a prior run already completed (and persisted) the
    # ANALYSIS phase, skip recomputing dead code / health / decisions — the
    # last is the costly one (LLM-backed, minutes on large repos). We rehydrate
    # only the thin views generation reads; the persisted rows stay
    # authoritative and are never re-written from these. ``health_report`` is
    # not a generation input, so it stays None on this path (its persisted rows
    # are untouched). Falls back to a full recompute if rehydration errors.
    skip_analysis = bool(
        resume_controller and await resume_controller.can_skip(ResumePhase.ANALYSIS)
    )
    dead_code_report = None
    health_report = None
    decision_report = None
    # Reports actually fed to generation + KG — rehydrated on the skip path,
    # the freshly computed ones otherwise.
    gen_dead_code_report = None
    gen_decision_report = None
    if skip_analysis:
        try:
            (
                gen_dead_code_report,
                gen_decision_report,
            ) = await resume_controller.rehydrate_analysis()
            if progress:
                progress.on_message("info", "  ↳ Resuming — reusing persisted analysis")
        except Exception as exc:
            logger.warning("resume_rehydrate_analysis_failed_recomputing", error=str(exc))
            skip_analysis = False

    if not skip_analysis:
        # The three analyses share read-only inputs (graph, git_meta_map,
        # parsed_files; the lazy metric caches were warmed during ingestion)
        # and have no data dependency on each other, so run them concurrently:
        # decision extraction is I/O/LLM-bound and its wall clock hides
        # entirely behind the CPU-bound dead-code + health work.
        dead_code_report, health_report, decision_report = await asyncio.gather(
            _run_dead_code_analysis(graph_builder, git_meta_map, progress=progress),
            _run_health_analysis(
                graph_builder,
                git_meta_map,
                parsed_files,
                repo_path=repo_path,
                coverage_report_paths=coverage_report_paths,
                progress=progress,
            ),
            _run_decision_extraction(
                repo_path,
                llm_client=llm_client,
                graph_builder=graph_builder,
                git_meta_map=git_meta_map,
                parsed_files=parsed_files,
                source_map=source_map,
                progress=progress,
            ),
        )

        # Drop the in-memory-only ``BlameIndex`` now that the health biomarkers
        # have consumed it — before it can leak into ``PipelineResult`` and the
        # downstream JSON artifact writers / DB persistence. ``git_meta_map``
        # shares these dict objects, so this cleans both views. Must stay after
        # the gather: health reads the blame index while it runs (decision
        # extraction never does).
        drop_transient_git_signals(git_metadata_list)
        gen_dead_code_report = dead_code_report
        gen_decision_report = decision_report

    # ---- Knowledge Graph skeleton (deterministic, no LLM) ----------------
    knowledge_graph_result = None
    try:
        from repowise.core.analysis.knowledge_graph import (
            KnowledgeGraphResult,
            build_knowledge_graph_skeleton,
            compute_kg_fingerprint,
            should_skip_kg_rebuild,
        )

        new_fingerprint = compute_kg_fingerprint(graph_builder)

        kg_json_path = repo_path / ".repowise" / "knowledge-graph.json"
        if should_skip_kg_rebuild(existing_kg_fingerprint, new_fingerprint, kg_json_path):
            knowledge_graph_result = KnowledgeGraphResult.from_file(kg_json_path)
            if knowledge_graph_result is not None:
                knowledge_graph_result.fingerprint = new_fingerprint
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

        tech_stack_dicts = [
            {"name": t.name, "version": t.version, "category": t.category} for t in tech_items
        ]

        if knowledge_graph_result is None:
            if progress:
                progress.on_phase_start("knowledge_graph.skeleton", None)
            knowledge_graph_result = build_knowledge_graph_skeleton(
                parsed_files=parsed_files,
                graph_builder=graph_builder,
                repo_structure=repo_structure,
                tech_stack=tech_stack_dicts,
                external_systems=external_systems,
                git_meta_map=git_meta_map,
                dead_code_report=gen_dead_code_report,
                repo_path=repo_path,
            )
            knowledge_graph_result.fingerprint = new_fingerprint
            if progress:
                progress.on_message(
                    "info",
                    f"  ↳ KG skeleton: {len(knowledge_graph_result.nodes)} nodes, "
                    f"{len(knowledge_graph_result.edges)} edges, "
                    f"{len(knowledge_graph_result.layers)} layers",
                )
        _phase_done(progress, "knowledge_graph.skeleton")

        # ---- KG curation/presentation pass (flagged, default on) ---------
        # Reshapes only the exported KG (layers/tour/entry-points/summaries);
        # never touches the AST graph, communities, or centrality. No-op when
        # REPOWISE_KG_CURATION is set to a falsy value (the raw uncurated
        # export). Runs in BOTH FAST and STANDARD (before the generate branch).
        if knowledge_graph_result is not None:
            from repowise.core.analysis.kg_curation import (
                curate_knowledge_graph,
                curation_enabled,
            )

            try:
                # In generate mode the summary floor is deferred to run after
                # the wiki-page backfill (in ``finalize_knowledge_graph``), so
                # rich page summaries win; FAST mode floors here.
                will_generate = generate_docs and llm_client is not None
                knowledge_graph_result = curate_knowledge_graph(
                    knowledge_graph_result,
                    parsed_files=parsed_files,
                    graph_builder=graph_builder,
                    repo_structure=repo_structure,
                    community_info=graph_builder.community_info(),
                    git_meta_map=git_meta_map,
                    enabled=curation_enabled(),
                    defer_summary_floor=will_generate,
                )
            except (ValueError, KeyError, RuntimeError) as cur_err:
                logger.error("kg_curation_failed", error=str(cur_err), exc_info=True)
    except (ValueError, KeyError, OSError, RuntimeError) as kg_err:
        logger.error("kg_skeleton_building_failed", error=str(kg_err), exc_info=True)

    # ---- Checkpoint: ANALYSIS ----------------------------------------------
    # Persist dead code + health + decisions now that the analysis phase is
    # complete, so an interrupt during the long generation phase below can
    # resume past analysis instead of recomputing it. Skipped when we already
    # rehydrated analysis (it's by definition persisted) — best-effort.
    if resume_controller is not None and not skip_analysis:
        await resume_controller.checkpoint_analysis(
            dead_code_report=dead_code_report,
            health_report=health_report,
            decision_report=decision_report,
            git_metadata_list=git_metadata_list,
        )

    # ---- Phase 3: Generation (optional) ------------------------------------
    generated_pages: list[Any] | None = None
    # Structural page types this run was authoritative for (see
    # PipelineResult.authoritative_page_types). Stays empty unless curated
    # generation engaged below.
    authoritative_page_types: set[str] = set()
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
        # legitimately emitted zero pages of it. Mirrors the selector's curated
        # engagement test (_build_curated_module_groups returns None — i.e.
        # falls back to community — only when kg_modules is empty) so the signal
        # is set iff the curated grouping actually engaged. On the degraded
        # community fallback both stay unset, preserving degradation honesty.
        kg_modules_present = bool(
            knowledge_graph_result is not None and knowledge_graph_result.modules
        )
        kg_layers_present = bool(
            knowledge_graph_result is not None and knowledge_graph_result.layers
        )
        module_grouping = getattr(resolved_generation_config, "module_grouping", "community")
        if module_grouping == "curated" and kg_modules_present:
            authoritative_page_types.add("module_page")
        if kg_layers_present:
            authoritative_page_types.add("layer_page")
        # A deterministic run takes every candidate rather than a budgeted
        # slice, so an SCC page missing from its output means the cycle is
        # gone, not that it lost a budget fight. That makes the run
        # authoritative for the type and lets the sweep clear cycle pages for
        # cycles that no longer exist. A budgeted run cannot claim the same:
        # zero SCC pages there may just mean the allocation was zero.
        if getattr(resolved_generation_config, "deterministic", False):
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
    _phase_done(progress, "graph.flows")

    # ---- Build result -------------------------------------------------------
    # Flush checkpoints on the success path; on an exception above this point
    # the checkpointer is intentionally not closed so interrupted phases stay
    # RUNNING for a resumable re-run.
    if _checkpointer is not None:
        await _checkpointer.aclose()

    elapsed = time.monotonic() - start
    languages = {fi.language for fi in file_infos if hasattr(fi, "language") and fi.language}
    symbol_count = sum(len(pf.symbols) for pf in parsed_files)

    return PipelineResult(
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
    )


# ---------------------------------------------------------------------------
# Phase helpers (private)
# ---------------------------------------------------------------------------
