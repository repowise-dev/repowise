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

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from repowise.core.ingestion.git_indexer._constants import _DEFAULT_COMMIT_LIMIT
from repowise.core.pipeline.modes import OrchestratorMode
from repowise.core.pipeline.progress import ProgressCallback

from ._run.analysis import run_analysis_phase
from ._run.generation import run_generation_phase, trace_execution_flows
from ._run.index import run_index_phase
from ._run.state import PipelineRun, attach_cost_tracker, hooked_progress, start_checkpointer

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

    Filled by the generation stage for symmetry, but the live route is the other one: ``--resume``
    only exists on ``init``, which calls ``run_pipeline(generate_docs=False)``
    and generates separately, so the init flows set this on the result
    themselves the same way they set ``generated_pages``. No caller currently
    reaches that assignment (it needs ``resume`` and ``generate_docs``
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

    await run_index_phase(run)
    await run_analysis_phase(run)
    await run_generation_phase(run)
    await trace_execution_flows(run)
    # Close only on success: after an exception the checkpointer stays open so
    # interrupted phases stay RUNNING for a resumable re-run.
    if checkpointer is not None:
        await checkpointer.aclose()
    return _build_result(run)


def _build_result(run: PipelineRun) -> PipelineResult:
    elapsed = time.monotonic() - run.start
    file_infos = run.file_infos
    parsed_files = run.parsed_files
    languages = {fi.language for fi in file_infos if hasattr(fi, "language") and fi.language}
    symbol_count = sum(pf.symbol_count for pf in parsed_files)
    resume_controller = run.resume_controller

    return PipelineResult(
        repo_path=str(run.repo_path),
        parsed_files=parsed_files,
        file_infos=file_infos,
        repo_structure=run.repo_structure,
        source_map=run.source_map,
        graph_builder=run.graph_builder,
        git_metadata_list=run.git_metadata_list,
        git_meta_map=run.git_meta_map,
        git_summary=run.git_summary,
        dead_code_report=run.dead_code_report,
        decision_report=run.decision_report,
        health_report=run.health_report,
        doc_drift_report=run.doc_drift_report,
        execution_flow_report=run.execution_flow_report,
        knowledge_graph_result=run.knowledge_graph_result,
        generated_pages=run.generated_pages,
        traversal_stats=run.traversal_stats,
        repo_name=run.repo_path.name,
        file_count=len(parsed_files),
        symbol_count=symbol_count,
        languages=languages,
        elapsed_seconds=elapsed,
        tech_stack=[
            {"name": t.name, "version": t.version, "category": t.category} for t in run.tech_items
        ],
        external_systems=run.external_systems,
        vector_store=run.vector_store,
        index_persisted_incrementally=(
            resume_controller.index_persisted if resume_controller is not None else False
        ),
        authoritative_page_types=run.authoritative_page_types,
        preserved_page_ids=run.preserved_page_ids,
    )

