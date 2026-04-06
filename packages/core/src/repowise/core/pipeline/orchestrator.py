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
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from repowise.core.pipeline.progress import ProgressCallback

logger = structlog.get_logger(__name__)

# Maximum seconds to spend on decision extraction before giving up.
# Large repos with tens of thousands of files can take arbitrarily long.
DECISION_EXTRACTION_TIMEOUT_SECS = 300


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
    generate_docs: bool = False,
    llm_client: Any | None = None,
    embedder: Any | None = None,
    vector_store: Any | None = None,
    concurrency: int = 5,
    test_run: bool = False,
    resume: bool = False,
    progress: ProgressCallback | None = None,
) -> PipelineResult:
    """Run the repowise indexing/analysis/generation pipeline.

    Parameters
    ----------
    repo_path:
        Path to an already-cloned repository on disk.
    commit_depth:
        Maximum commits to analyse per file (1-5000). Default 500.
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

    Returns
    -------
    PipelineResult
        All pipeline outputs held in memory.
    """
    repo_path = Path(repo_path).resolve()
    start = time.monotonic()

    commit_depth = max(1, min(commit_depth, 5000))

    # ---- Phase 1: Ingestion ------------------------------------------------
    if progress:
        progress.on_message("info", "Phase 1: Ingestion")

    parsed_files, file_infos, repo_structure, source_map, graph_builder = (
        await _run_ingestion(
            repo_path,
            exclude_patterns=exclude_patterns,
            skip_tests=skip_tests,
            skip_infra=skip_infra,
            progress=progress,
        )
    )

    # Git indexing (runs concurrently with ingestion in the CLI, but here
    # we start it after traversal since we're already async)
    git_summary, git_metadata_list, git_meta_map = await _run_git_indexing(
        repo_path,
        commit_depth=commit_depth,
        follow_renames=follow_renames,
        progress=progress,
    )

    # Add co-change edges to the graph
    if git_meta_map:
        graph_builder.add_co_change_edges(git_meta_map)

    if progress:
        progress.on_message(
            "info",
            f"Ingested {len(parsed_files)} files"
            + (
                f" · Git: {git_summary.files_indexed} files"
                if git_summary and git_summary.files_indexed
                else ""
            ),
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

    dead_code_report = await _run_dead_code_analysis(
        graph_builder, git_meta_map, progress=progress
    )

    decision_report = await _run_decision_extraction(
        repo_path,
        llm_client=llm_client,
        graph_builder=graph_builder,
        git_meta_map=git_meta_map,
        parsed_files=parsed_files,
        progress=progress,
    )

    # ---- Phase 3: Generation (optional) ------------------------------------
    generated_pages: list[Any] | None = None
    if generate_docs and llm_client is not None:
        if progress:
            progress.on_message("info", "Phase 3: Generation")

        generated_pages = await run_generation(
            repo_path=repo_path,
            parsed_files=parsed_files,
            source_map=source_map,
            graph_builder=graph_builder,
            repo_structure=repo_structure,
            git_meta_map=git_meta_map,
            llm_client=llm_client,
            embedder=embedder,
            vector_store=vector_store,
            concurrency=concurrency,
            progress=progress,
            resume=resume,
        )

    # ---- Build result -------------------------------------------------------
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
        generated_pages=generated_pages,
        repo_name=repo_path.name,
        file_count=len(parsed_files),
        symbol_count=symbol_count,
        languages=languages,
        elapsed_seconds=elapsed,
    )


# ---------------------------------------------------------------------------
# Phase helpers (private)
# ---------------------------------------------------------------------------


async def _run_ingestion(
    repo_path: Path,
    *,
    exclude_patterns: list[str] | None,
    skip_tests: bool,
    skip_infra: bool,
    progress: ProgressCallback | None,
) -> tuple[list[Any], list[Any], Any, dict[str, bytes], Any]:
    """Traverse, parse, and build the dependency graph.

    Returns (parsed_files, file_infos, repo_structure, source_map, graph_builder).
    """
    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    traverser = FileTraverser(repo_path, extra_exclude_patterns=exclude_patterns or None)

    # Walk directory tree
    all_paths = list(traverser._walk())
    if progress:
        progress.on_phase_start("traverse", len(all_paths))

    # Parallel stat + header reads (I/O bound).
    # Use asyncio.wrap_future so the event loop stays responsive while waiting.
    file_infos: list[Any] = []
    io_pool = ThreadPoolExecutor(max_workers=8)
    try:
        aws = [
            asyncio.wrap_future(io_pool.submit(traverser._build_file_info, p))
            for p in all_paths
        ]
        for coro in asyncio.as_completed(aws):
            try:
                result = await coro
            except Exception:
                result = None
            if result is not None:
                file_infos.append(result)
            if progress:
                progress.on_item_done("traverse")
    finally:
        # shutdown(wait=True) is blocking — run in a thread to keep the
        # event loop responsive.  All submitted futures have already
        # completed by the time we reach here (the for-loop awaited them).
        await asyncio.to_thread(io_pool.shutdown, wait=True)

    repo_structure = traverser.get_repo_structure(file_infos)

    # Filter
    if skip_tests:
        file_infos = [fi for fi in file_infos if not fi.is_test]
    if skip_infra:
        file_infos = [
            fi
            for fi in file_infos
            if fi.language not in ("dockerfile", "makefile", "terraform", "shell")
        ]

    # Parse (sequential — GraphBuilder is not thread-safe)
    if progress:
        progress.on_phase_start("parse", len(file_infos))

    parser = ASTParser()
    parsed_files: list[Any] = []
    source_map: dict[str, bytes] = {}
    graph_builder = GraphBuilder(repo_path=repo_path)

    for i, fi in enumerate(file_infos):
        try:
            source = Path(fi.abs_path).read_bytes()
            parsed = parser.parse_file(fi, source)
            parsed_files.append(parsed)
            source_map[fi.path] = source
            graph_builder.add_file(parsed)
        except Exception:
            pass  # skip unparseable files
        if progress:
            progress.on_item_done("parse")
        # Yield control every 50 files so the event loop stays responsive
        if i % 50 == 49:
            await asyncio.sleep(0)

    # Build graph (CPU-bound — run in thread to keep event loop free)
    if progress:
        progress.on_phase_start("graph", 1)
    await asyncio.to_thread(graph_builder.build)

    # Add framework-aware synthetic edges (conftest, Django, FastAPI, Flask)
    try:
        from repowise.core.generation.editor_files.tech_stack import detect_tech_stack

        tech_items = detect_tech_stack(repo_path)
        graph_builder.add_framework_edges([item.name for item in tech_items])
    except Exception:
        pass  # framework edge detection is best-effort

    if progress:
        progress.on_item_done("graph")

    # Report oversized file skips
    if traverser._oversized_skip_count and progress:
        progress.on_message(
            "warning",
            f"Skipped {traverser._oversized_skip_count} oversized files "
            f"(>{traverser.max_file_size_bytes // 1024} KB)",
        )
        for path, size_bytes in sorted(
            traverser._oversized_files, key=lambda x: x[1], reverse=True
        ):
            logger.info(
                "oversized_file_skipped",
                path=path,
                size_kb=size_bytes // 1024,
            )

    return parsed_files, file_infos, repo_structure, source_map, graph_builder


async def _run_git_indexing(
    repo_path: Path,
    *,
    commit_depth: int,
    follow_renames: bool,
    progress: ProgressCallback | None,
) -> tuple[Any | None, list[dict], dict[str, dict]]:
    """Run git history indexing.

    Returns (git_summary, git_metadata_list, git_meta_map).
    """
    try:
        from repowise.core.ingestion.git_indexer import GitIndexer

        git_indexer = GitIndexer(
            repo_path,
            commit_limit=commit_depth,
            follow_renames=follow_renames,
        )

        def _on_start(total: int) -> None:
            if progress:
                progress.on_phase_start("git", total)

        def _on_file_done() -> None:
            if progress:
                progress.on_item_done("git")

        def _on_co_change_start(total: int) -> None:
            if progress:
                progress.on_phase_start("co_change", total)

        def _on_commit_done() -> None:
            if progress:
                progress.on_item_done("co_change")

        git_summary, git_metadata_list = await git_indexer.index_repo(
            "",
            on_start=_on_start,
            on_file_done=_on_file_done,
            on_co_change_start=_on_co_change_start,
            on_commit_done=_on_commit_done,
        )
        git_meta_map = {m["file_path"]: m for m in git_metadata_list}
        return git_summary, git_metadata_list, git_meta_map
    except Exception as exc:
        if progress:
            progress.on_message("warning", f"Git indexing skipped: {exc}")
        return None, [], {}


async def _run_dead_code_analysis(
    graph_builder: Any,
    git_meta_map: dict[str, dict],
    *,
    progress: ProgressCallback | None,
) -> Any | None:
    """Run dead code detection (pure graph traversal, no LLM)."""
    try:
        from repowise.core.analysis.dead_code import DeadCodeAnalyzer

        if progress:
            progress.on_phase_start("dead_code", None)

        analyzer = DeadCodeAnalyzer(graph_builder.graph(), git_meta_map)
        report = analyzer.analyze()

        if progress:
            unreachable = sum(1 for f in report.findings if f.kind.value == "unreachable_file")
            unused_exports = sum(1 for f in report.findings if f.kind.value == "unused_export")
            progress.on_message(
                "info",
                f"Dead code: {unreachable} unreachable files "
                f"· {unused_exports} unused exports (~{report.deletable_lines:,} lines)",
            )

        return report
    except Exception as exc:
        if progress:
            progress.on_message("warning", f"Dead code detection skipped: {exc}")
        return None


async def _run_decision_extraction(
    repo_path: Path,
    *,
    llm_client: Any | None,
    graph_builder: Any,
    git_meta_map: dict[str, dict],
    parsed_files: list[Any],
    progress: ProgressCallback | None,
) -> Any | None:
    """Extract architectural decisions from source and git history."""
    try:
        from repowise.core.analysis.decision_extractor import DecisionExtractor

        if progress:
            progress.on_phase_start("decisions", None)

        extractor = DecisionExtractor(
            repo_path=repo_path,
            provider=llm_client,
            graph=graph_builder.graph(),
            git_meta_map=git_meta_map,
            parsed_files=parsed_files,
        )
        report = await asyncio.wait_for(extractor.extract_all(), timeout=DECISION_EXTRACTION_TIMEOUT_SECS)

        if progress:
            inline = report.by_source.get("inline_marker", 0)
            readme = report.by_source.get("readme_mining", 0)
            git_arch = report.by_source.get("git_archaeology", 0)
            progress.on_message(
                "info",
                f"Decisions: {inline} inline · {readme} from docs · {git_arch} from git",
            )

        return report
    except Exception as exc:
        if progress:
            progress.on_message("warning", f"Decision extraction skipped: {exc}")
        return None


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
) -> list[Any]:
    """Run LLM-powered page generation.

    Returns a list of ``GeneratedPage`` objects.
    """
    from repowise.core.generation import (
        ContextAssembler,
        GenerationConfig,
        JobSystem,
        PageGenerator,
    )
    from repowise.core.persistence.vector_store import InMemoryVectorStore
    from repowise.core.providers.embedding.base import MockEmbedder

    config = GenerationConfig(max_concurrency=concurrency)
    assembler = ContextAssembler(config)

    # Resolve embedder and vector store
    embedder_impl = embedder if embedder is not None else MockEmbedder()

    if vector_store is None:
        vector_store = InMemoryVectorStore(embedder_impl)

    generator = PageGenerator(llm_client, assembler, config, vector_store=vector_store)

    # Job system — use a temp-like dir under repo_path for checkpoints
    jobs_dir = repo_path / ".repowise" / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    job_system = JobSystem(jobs_dir)

    repo_name = repo_path.name

    # Track generation progress
    _pages_done = 0

    def on_page_done(page_type: str) -> None:
        nonlocal _pages_done
        _pages_done += 1
        if progress:
            progress.on_item_done("generation")

    if progress:
        progress.on_phase_start("generation", None)

    def on_total_known(total: int) -> None:
        if progress:
            progress.on_phase_start("generation", total)

    generated_pages = await generator.generate_all(
        parsed_files,
        source_map,
        graph_builder,
        repo_structure,
        repo_name,
        job_system=job_system,
        on_page_done=on_page_done,
        on_total_known=on_total_known,
        git_meta_map=git_meta_map if git_meta_map else None,
        resume=resume,
        repo_path=repo_path,
    )

    if progress:
        progress.on_message("info", f"Generated {len(generated_pages)} pages")

    return generated_pages
