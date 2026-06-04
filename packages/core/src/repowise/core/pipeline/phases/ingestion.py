"""Pipeline ingestion phase.

Extracted from the former monolithic ``orchestrator.py``; ``run_pipeline`` (in
orchestrator.py) imports these phase functions. No CLI/click/rich imports.
"""

from __future__ import annotations

import asyncio
import os
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import structlog

from repowise.core.pipeline.progress import ProgressCallback

from ._common import _phase_done

logger = structlog.get_logger(__name__)


async def _timed_step(
    label: str,
    fn: Any,
    progress: ProgressCallback | None,
) -> Any:
    """Run *fn* in a worker thread and emit a per-step completion line.

    Used to make ``asyncio.gather`` of multiple graph-algorithm calls
    legible: without this, four concurrent thread-bound computations
    (e.g. PageRank + betweenness + symbol PageRank + symbol betweenness)
    appear in the CLI as one opaque several-minute spinner, so the user
    has no signal as to which step is the bottleneck. With it, each algo
    prints `  ↳ <label> ✓ (Xs)` as it finishes — completion order
    surfaces the relative cost without changing the underlying execution.
    """
    t0 = time.monotonic()
    try:
        result = await asyncio.to_thread(fn)
    except Exception as exc:
        if progress is not None:
            progress.on_message(
                "warning",
                f"  ↳ {label} failed after {time.monotonic() - t0:.1f}s: {exc}",
            )
        raise
    if progress is not None:
        progress.on_message(
            "info",
            f"  ↳ {label} ✓ ({time.monotonic() - t0:.1f}s)",
        )
    return result


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


def _read_sources(
    file_infos: list[Any],
    progress: ProgressCallback | None,
) -> list[tuple]:
    """Read source bytes for *file_infos* with a thread pool (I/O-bound).

    Returns ``(FileInfo, bytes)`` tuples in the same order as the input —
    the parse aggregation loop indexes positionally. Files that fail to
    read are dropped, ticking the parse bar once each (matching the old
    sequential loop's behavior).
    """

    def _read_one(fi: Any) -> tuple | None:
        try:
            return (fi, Path(fi.abs_path).read_bytes())
        except Exception:
            if progress:
                progress.on_item_done("parse")
            return None

    if not file_infos:
        return []
    workers = min(32, max(4, (os.cpu_count() or 4) * 2))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_read_one, file_infos))
    return [r for r in results if r is not None]


async def _run_ingestion(
    repo_path: Path,
    *,
    exclude_patterns: list[str] | None,
    include_submodules: bool = False,
    include_nested_repos: bool = False,
    skip_tests: bool,
    skip_infra: bool,
    progress: ProgressCallback | None,
) -> tuple[list[Any], list[Any], Any, dict[str, bytes], Any, Any]:
    """Traverse, parse, and build the dependency graph.

    Returns (parsed_files, file_infos, repo_structure, source_map,
    graph_builder, traversal_stats, tech_items).
    """
    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    traverser = FileTraverser(
        repo_path,
        extra_exclude_patterns=exclude_patterns or None,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
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
    _phase_done(progress, "traverse")

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

    # Read source bytes up front in a thread pool (I/O-bound; keeps worker
    # args small: FileInfo + bytes, both picklable plain dataclasses/bytes).
    fi_and_bytes: list[tuple] = _read_sources(file_infos, progress)

    parsed_files: list[Any] = []
    source_map: dict[str, bytes] = {}
    graph_builder = GraphBuilder(repo_path=repo_path, exclude_patterns=exclude_patterns)

    loop = asyncio.get_running_loop()
    workers = max(1, os.cpu_count() or 4)

    _use_process_pool = True
    parse_results: list[Any] = []

    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            tasks = [loop.run_in_executor(pool, _parse_one, item) for item in fi_and_bytes]
            # Tick the parse-progress bar as each worker finishes —
            # ``asyncio.gather`` would otherwise hold every event back
            # until the last file is done, which on PowerToys-scale
            # repos looked like a hang at ``0/N`` for many minutes.
            # Per-task done-callbacks fire on the event loop thread and
            # preserve gather's ordered results, so the aggregation
            # loop below still indexes ``fi_and_bytes`` correctly.
            if progress is not None:
                _parse_tick = lambda _fut: progress.on_item_done("parse")  # noqa: E731
                for fut in tasks:
                    fut.add_done_callback(_parse_tick)
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
        # Process-pool path already ticked per-file via the done-callback
        # attached above; only the fallback path ticks here (handled in
        # its own loop). No tick needed in aggregation.

    _phase_done(progress, "parse")

    # ---- tsconfig path-alias resolver (before graph build) ------------------
    # Only runs when the repo has TS/JS files. On large TS monorepos the
    # resolver indexes hundreds of tsconfig files up-front; without a phase
    # label this shows up as a silent gap right after parsing.
    try:
        from repowise.core.ingestion.tsconfig_resolver import TsconfigResolver

        _ts_langs = {"typescript", "javascript"}
        if any(pf.file_info.language in _ts_langs for pf in parsed_files):
            if progress:
                progress.on_phase_start("tsconfig", None)
            _path_set = set(graph_builder._parsed_files.keys())
            _resolver = TsconfigResolver(repo_path=repo_path, path_set=_path_set)
            graph_builder.set_tsconfig_resolver(_resolver)
            _phase_done(progress, "tsconfig")
    except Exception as _resolver_exc:
        logger.warning("tsconfig_resolver_init_failed", error=str(_resolver_exc))

    # ---- Graph build phase -------------------------------------------------
    # Sub-phases (graph.imports / graph.heritage / graph.calls) are emitted
    # from inside GraphBuilder.build(); the orchestrator drives metrics/
    # communities/flows below so the longest-running step is no longer an
    # opaque "graph 0/1" spinner.
    if progress:
        progress.on_message(
            "info",
            "  (graph build can take several minutes on first run — safe to "
            "Ctrl-C, then run 'repowise init --resume' to continue)",
        )
    await asyncio.to_thread(graph_builder.build, progress)

    # Add framework-aware synthetic edges (conftest, Django, FastAPI, Flask)
    tech_items: list = []
    try:
        from repowise.core.generation.editor_files.tech_stack import detect_tech_stack

        tech_items = detect_tech_stack(repo_path)
        graph_builder.add_framework_edges([item.name for item in tech_items])
    except Exception:
        pass  # framework edge detection is best-effort

    # ---- Dynamic hints wiring (after static graph is fully built) ----------
    if progress:
        progress.on_phase_start("dynamic_hints", None)
    try:
        from repowise.core.ingestion.dynamic_hints import HintRegistry

        registry = HintRegistry()
        dynamic_edges = await loop.run_in_executor(None, registry.extract_all, repo_path)
        graph_builder.add_dynamic_edges(dynamic_edges)
        logger.info("dynamic_hints_added", count=len(dynamic_edges))
    except Exception as hints_exc:
        logger.warning("dynamic_hints_failed", error=str(hints_exc))
    _phase_done(progress, "dynamic_hints")

    # ---- Graph metrics: prime caches with live progress ---------------------
    # pagerank/betweenness/community/symbol_communities/execution_flows are
    # otherwise computed lazily during persist + generation, where they hide
    # behind opaque progress bars. Pre-compute them here so each is its own
    # visible sub-phase, and fan the within-phase work out via
    # asyncio.gather so betweenness (the dominant cost) overlaps with
    # PageRank / community detection rather than running serially.
    #
    # Each algorithm is wrapped in ``_timed_step`` so we emit a per-algo
    # completion line (`  ↳ PageRank ✓ (Xs)`) as it finishes. Without these,
    # the whole gather looks like one opaque several-minute void where
    # betweenness centrality dominates — splitting the timing makes it
    # obvious which step is the bottleneck on a given repo.
    if progress:
        progress.on_phase_start("graph.metrics", None)
    await asyncio.gather(
        _timed_step("PageRank", graph_builder.pagerank, progress),
        _timed_step("betweenness centrality", graph_builder.betweenness_centrality, progress),
        _timed_step("symbol PageRank", graph_builder.symbol_pagerank, progress),
        _timed_step("symbol betweenness", graph_builder.symbol_betweenness_centrality, progress),
    )
    _phase_done(progress, "graph.metrics")

    if progress:
        progress.on_phase_start("graph.communities", None)
    await asyncio.gather(
        _timed_step("community detection", graph_builder.community_detection, progress),
        _timed_step("symbol communities", graph_builder.symbol_communities, progress),
    )
    _phase_done(progress, "graph.communities")

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
        if stats.skipped_nested_repo:
            parts.append(f"{stats.skipped_nested_repo:,} nested git repos")
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

    return parsed_files, file_infos, repo_structure, source_map, graph_builder, stats, tech_items


async def reparse_for_resume(
    repo_path: Path,
    *,
    exclude_patterns: list[str] | None,
    include_submodules: bool = False,
    include_nested_repos: bool = False,
    skip_tests: bool,
    skip_infra: bool,
    progress: ProgressCallback | None,
) -> tuple[list[Any], list[Any], Any, dict[str, bytes], list[Any]]:
    """Parse-only ingestion for a resumed run: traverse + parse, **no graph
    build or centrality**.

    A resumed run rehydrates the graph (and its expensive centrality metrics)
    and the git metadata from the database, so the only thing it must redo on
    disk is parsing source into ``ParsedFile`` objects (those aren't persisted
    in reconstructable form, and the analysis/generation phases need them).
    Skipping the graph build + the four centrality kernels is exactly the
    minutes-long work resume exists to avoid.

    Returns ``(parsed_files, file_infos, repo_structure, source_map,
    tech_items)`` — mirrors the prefix of :func:`_run_ingestion` minus the
    graph builder and traversal stats.
    """
    from repowise.core.ingestion import ASTParser, FileTraverser

    traverser = FileTraverser(
        repo_path,
        extra_exclude_patterns=exclude_patterns or None,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
    )

    all_paths = list(traverser._walk())
    if progress:
        progress.on_phase_start("traverse", None)

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
        await asyncio.to_thread(io_pool.shutdown, wait=True)

    repo_structure = traverser.get_repo_structure(file_infos)
    _phase_done(progress, "traverse")

    if skip_tests:
        file_infos = [fi for fi in file_infos if not fi.is_test]
    if skip_infra:
        file_infos = [
            fi
            for fi in file_infos
            if fi.language not in ("dockerfile", "makefile", "terraform", "shell")
        ]

    if progress:
        progress.on_phase_start("parse", len(file_infos))

    fi_and_bytes: list[tuple] = _read_sources(file_infos, progress)

    parsed_files: list[Any] = []
    source_map: dict[str, bytes] = {}
    loop = asyncio.get_running_loop()
    workers = max(1, os.cpu_count() or 4)
    parse_results: list[Any] = []

    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            tasks = [loop.run_in_executor(pool, _parse_one, item) for item in fi_and_bytes]
            if progress is not None:
                _tick = lambda _fut: progress.on_item_done("parse")  # noqa: E731
                for fut in tasks:
                    fut.add_done_callback(_tick)
            parse_results = await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as pool_exc:
        logger.warning("resume_reparse_pool_failed_falling_back", error=str(pool_exc))
        _fallback_parser = ASTParser()
        for i, (fi, source) in enumerate(fi_and_bytes):
            try:
                parse_results.append(_fallback_parser.parse_file(fi, source))
            except Exception as exc:
                parse_results.append((fi.abs_path, str(exc)))
            if progress:
                progress.on_item_done("parse")
            if i % 50 == 49:
                await asyncio.sleep(0)

    for idx, result in enumerate(parse_results):
        fi, source = fi_and_bytes[idx]
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], str):
            logger.debug("parse_error_in_worker", path=result[0], error=result[1])
        elif isinstance(result, Exception):
            logger.debug("parse_exception_in_worker", path=fi.abs_path, error=str(result))
        else:
            parsed_files.append(result)
            source_map[fi.path] = source
    _phase_done(progress, "parse")

    tech_items: list = []
    try:
        from repowise.core.generation.editor_files.tech_stack import detect_tech_stack

        tech_items = detect_tech_stack(repo_path)
    except Exception:
        pass

    return parsed_files, file_infos, repo_structure, source_map, tech_items
