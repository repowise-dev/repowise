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
import os
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from repowise.core.pipeline.progress import ProgressCallback

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Process-pool worker (module-level — must be picklable)
# ---------------------------------------------------------------------------

# Module-level process-local parser cache (one per worker process).
_WORKER_PARSER: Any = None


def _parse_one(path_and_fi_and_bytes: tuple) -> Any:
    """Worker function for ProcessPoolExecutor parsing.

    Constructs (or reuses) a process-local ASTParser and parses one file.
    Returns a ParsedFile on success, or (abs_path_str, error_str) on failure.
    The parser is constructed lazily inside the worker — the ASTParser itself
    (which holds compiled tree-sitter Language/Query objects) is never pickled.
    Only FileInfo (input) and ParsedFile (output) cross the process boundary;
    both are plain dataclasses and therefore picklable.
    """
    global _WORKER_PARSER
    fi, source = path_and_fi_and_bytes
    try:
        if _WORKER_PARSER is None:
            from repowise.core.ingestion import ASTParser

            _WORKER_PARSER = ASTParser()
        return _WORKER_PARSER.parse_file(fi, source)
    except Exception as exc:
        return (fi.abs_path, str(exc))


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

    # Traversal stats
    traversal_stats: Any | None = None
    """``TraversalStats`` from the file traverser, or None."""


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
    generate_docs: bool = False,
    llm_client: Any | None = None,
    embedder: Any | None = None,
    vector_store: Any | None = None,
    concurrency: int = 5,
    test_run: bool = False,
    resume: bool = False,
    progress: ProgressCallback | None = None,
    cost_tracker: Any | None = None,
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
            progress=progress,
        )

    async def _ingestion_stage() -> tuple:
        return await _run_ingestion(
            repo_path,
            exclude_patterns=exclude_patterns,
            include_submodules=include_submodules,
            skip_tests=skip_tests,
            skip_infra=skip_infra,
            progress=progress,
        )

    (
        (
            parsed_files,
            file_infos,
            repo_structure,
            source_map,
            graph_builder,
            traversal_stats,
        ),
        (
            git_summary,
            git_metadata_list,
            git_meta_map,
        ),
    ) = await asyncio.gather(_ingestion_stage(), _git_stage())

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

    dead_code_report = await _run_dead_code_analysis(graph_builder, git_meta_map, progress=progress)

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
        traversal_stats=traversal_stats,
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
    include_submodules: bool = False,
    skip_tests: bool,
    skip_infra: bool,
    progress: ProgressCallback | None,
) -> tuple[list[Any], list[Any], Any, dict[str, bytes], Any, Any]:
    """Traverse, parse, and build the dependency graph.

    Returns (parsed_files, file_infos, repo_structure, source_map, graph_builder, traversal_stats).
    """
    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    traverser = FileTraverser(
        repo_path,
        extra_exclude_patterns=exclude_patterns or None,
        include_submodules=include_submodules,
    )

    # Walk directory tree
    all_paths = list(traverser._walk())
    if progress:
        # Use indeterminate progress (spinner) to avoid showing a misleading
        # pre-filter total like "2132/83601".
        progress.on_phase_start("traverse", None)

    # Parallel stat + header reads (I/O bound).
    # Use asyncio.wrap_future so the event loop stays responsive while waiting.
    file_infos: list[Any] = []
    io_pool = ThreadPoolExecutor(max_workers=8)
    try:
        aws = [
            asyncio.wrap_future(io_pool.submit(traverser._build_file_info, p)) for p in all_paths
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

    # ---- Parse phase: CPU-bound, run in ProcessPoolExecutor ----------------
    if progress:
        progress.on_phase_start("parse", len(file_infos))

    # Read source bytes up front (I/O, sequential — fast enough; keeps worker
    # args small: FileInfo + bytes, both picklable plain dataclasses/bytes).
    fi_and_bytes: list[tuple] = []
    for fi in file_infos:
        try:
            source = Path(fi.abs_path).read_bytes()
            fi_and_bytes.append((fi, source))
        except Exception:
            if progress:
                progress.on_item_done("parse")

    parsed_files: list[Any] = []
    source_map: dict[str, bytes] = {}
    graph_builder = GraphBuilder(repo_path=repo_path)

    loop = asyncio.get_running_loop()
    workers = max(1, os.cpu_count() or 4)

    _use_process_pool = True
    parse_results: list[Any] = []

    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            tasks = [loop.run_in_executor(pool, _parse_one, item) for item in fi_and_bytes]
            # Use as_completed via asyncio.as_completed to report per-file progress.
            # We need to preserve (task → fi_and_bytes index) for source_map so we
            # wrap tasks in a list and drain with gather instead.
            parse_results = await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as pool_exc:
        logger.warning(
            "process_pool_parse_failed_falling_back",
            error=str(pool_exc),
        )
        _use_process_pool = False
        # Fallback: in-process sequential parse
        _fallback_parser = ASTParser()
        for i, (fi, source) in enumerate(fi_and_bytes):
            try:
                result = _fallback_parser.parse_file(fi, source)
                parse_results.append(result)
            except Exception as exc:
                parse_results.append((fi.abs_path, str(exc)))
            if progress:
                progress.on_item_done("parse")
            if i % 50 == 49:
                await asyncio.sleep(0)

    # Aggregate results into GraphBuilder on the main loop (not thread-safe).
    for idx, result in enumerate(parse_results):
        fi, source = fi_and_bytes[idx]
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], str):
            # Error tuple: (abs_path_str, error_str)
            logger.debug("parse_error_in_worker", path=result[0], error=result[1])
        elif isinstance(result, Exception):
            logger.debug("parse_exception_in_worker", path=fi.abs_path, error=str(result))
        else:
            parsed_files.append(result)
            source_map[fi.path] = source
            graph_builder.add_file(result)
        # Report per-file progress if we used the process pool (fallback already reported above).
        if _use_process_pool and progress:
            progress.on_item_done("parse")

    # ---- tsconfig path-alias resolver (before graph build) ------------------
    try:
        from repowise.core.ingestion.tsconfig_resolver import TsconfigResolver

        _ts_langs = {"typescript", "javascript"}
        if any(pf.file_info.language in _ts_langs for pf in parsed_files):
            _path_set = set(graph_builder._parsed_files.keys())
            _resolver = TsconfigResolver(repo_path=repo_path, path_set=_path_set)
            graph_builder.set_tsconfig_resolver(_resolver)
    except Exception as _resolver_exc:
        logger.warning("tsconfig_resolver_init_failed", error=str(_resolver_exc))

    # ---- Graph build phase -------------------------------------------------
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

    # ---- Dynamic hints wiring (after static graph is fully built) ----------
    try:
        from repowise.core.ingestion.dynamic_hints import HintRegistry

        registry = HintRegistry()
        dynamic_edges = await loop.run_in_executor(None, registry.extract_all, repo_path)
        graph_builder.add_dynamic_edges(dynamic_edges)
        logger.info("dynamic_hints_added", count=len(dynamic_edges))
    except Exception as hints_exc:
        logger.warning("dynamic_hints_failed", error=str(hints_exc))

    if progress:
        progress.on_item_done("graph")

    # Emit filtering summary so users can see what was included/excluded
    stats = traverser.stats
    if progress:
        parts: list[str] = []
        if stats.skipped_gitignore:
            parts.append(f"{stats.skipped_gitignore:,} by .gitignore")
        if stats.skipped_blocked_extension:
            parts.append(f"{stats.skipped_blocked_extension:,} by extension")
        if stats.skipped_blocked_pattern:
            parts.append(f"{stats.skipped_blocked_pattern:,} by filename pattern")
        if stats.skipped_oversized:
            parts.append(f"{stats.skipped_oversized:,} oversized")
        if stats.skipped_binary:
            parts.append(f"{stats.skipped_binary:,} binary")
        if stats.skipped_generated:
            parts.append(f"{stats.skipped_generated:,} generated")
        if stats.skipped_extra_exclude:
            parts.append(f"{stats.skipped_extra_exclude:,} by --exclude")
        if stats.skipped_extra_ignore:
            parts.append(f"{stats.skipped_extra_ignore:,} by .repowiseIgnore")
        if stats.skipped_submodule:
            parts.append(f"{stats.skipped_submodule:,} submodule dirs")
        if stats.skipped_unknown_language:
            parts.append(f"{stats.skipped_unknown_language:,} unknown type")

        excluded_str = ", ".join(parts) if parts else "none"
        progress.on_message(
            "info",
            f"Scanned {stats.total_paths_walked:,} files, {len(file_infos):,} included",
        )
        if parts:
            progress.on_message("info", f"  Excluded: {excluded_str}")

        # Language breakdown
        if stats.lang_counts:
            top_langs = sorted(stats.lang_counts.items(), key=lambda x: -x[1])[:6]
            lang_str = ", ".join(f"{lang} {count:,}" for lang, count in top_langs)
            rest_count = sum(
                c for _, c in sorted(stats.lang_counts.items(), key=lambda x: -x[1])[6:]
            )
            if rest_count:
                lang_str += f", other {rest_count:,}"
            progress.on_message("info", f"  Languages: {lang_str}")

    return parsed_files, file_infos, repo_structure, source_map, graph_builder, stats


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
        report = await asyncio.wait_for(
            extractor.extract_all(), timeout=DECISION_EXTRACTION_TIMEOUT_SECS
        )

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
    cost_tracker: Any | None = None,
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

    # Attach cost tracker to LLM client if available
    if cost_tracker is not None and llm_client is not None and hasattr(llm_client, "_cost_tracker"):
        llm_client._cost_tracker = cost_tracker

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
            # Push live cost update if the callback supports it
            if cost_tracker is not None and hasattr(progress, "set_cost"):
                progress.set_cost(cost_tracker.session_cost)

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
