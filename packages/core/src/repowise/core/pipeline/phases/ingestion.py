"""Pipeline ingestion phase.

Extracted from the former monolithic ``orchestrator.py``; ``run_pipeline`` (in
orchestrator.py) imports these phase functions. No CLI/click/rich imports.
"""

from __future__ import annotations

import asyncio
import contextlib
import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import structlog

from repowise.core.pipeline.progress import ProgressCallback, emit_warning

from ._common import _phase_done

logger = structlog.get_logger(__name__)


def _report_missing_grammars(stats: Any, progress: ProgressCallback | None) -> None:
    """Name the languages present in this repo that have no installed grammar.

    Scoped to what the traversal actually found, so a machine missing a grammar
    for a language the repo does not contain says nothing. Best-effort: a
    reporting failure must not stop an index.
    """
    if progress is None or stats is None:
        return
    lang_counts = getattr(stats, "lang_counts", None)
    if not lang_counts:
        return
    try:
        from repowise.core.ingestion.parser import missing_grammar_languages

        missing = missing_grammar_languages(lang_counts)
    except Exception:
        logger.debug("grammar_preflight_failed", exc_info=True)
        return
    if not missing:
        return
    detail = ", ".join(f"{lang} ({lang_counts[lang]:,} files)" for lang in missing)
    emit_warning(
        progress,
        f"No parser installed for: {detail}. "
        "These files are indexed but contribute no symbols; "
        "reinstall repowise to pick up the missing grammars.",
    )


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


# Traversal skip counters, in report order: (stat attribute, how to describe it).
# A table rather than eleven near-identical ``if`` blocks, so adding a skip
# reason to the traverser is one line here.
_SKIP_REASONS: tuple[tuple[str, str], ...] = (
    ("skipped_gitignore", "by .gitignore"),
    ("skipped_blocked_extension", "by extension"),
    ("skipped_blocked_pattern", "by filename pattern"),
    ("skipped_oversized", "oversized"),
    ("skipped_binary", "binary"),
    ("skipped_generated", "generated"),
    ("skipped_extra_exclude", "by --exclude"),
    ("skipped_extra_ignore", "by .repowiseIgnore"),
    ("skipped_submodule", "submodule dirs"),
    ("skipped_nested_repo", "nested git repos"),
    ("skipped_unknown_language", "unknown type"),
)

_TOP_LANGUAGES_SHOWN = 6

# Files at or above which the graph build is slow enough to be worth warning
# about. Below it the build finishes in around a second, so the Ctrl-C
# reassurance would be noise on a run that never gets a chance to be
# interrupted. Measured against a 157-file repo (0.8s) and the calibration
# behind the pre-scan estimate, roughly 2 min per 1k source files end to end.
_SLOW_GRAPH_BUILD_FILES = 2000


async def _record_structural_episodes(
    repo_path: Path,
    traverser: Any,
    *,
    allow_formatter_check: bool,
) -> None:
    """Persist structural episodes for the walk that just finished.

    Imported lazily and swallowed whole: an episode is enrichment, and nothing
    here may fail or slow an index. Off the event loop because the formatter
    check is a blocking subprocess with a timeout, and this loop is also
    driving progress rendering.
    """
    try:
        from repowise.core.precedent.structural import record_structural_episodes

        await asyncio.to_thread(
            record_structural_episodes,
            repo_path,
            traverser,
            allow_formatter_check=allow_formatter_check,
        )
    except Exception:
        logger.debug("structural_episodes_skipped", exc_info=True)


def _emit_traversal_summary(
    progress: ProgressCallback | None,
    stats: Any,
    included: int,
) -> None:
    """Report what the walk found and what it left out."""
    if progress is None:
        return

    progress.on_message(
        "info", f"Scanned {stats.total_paths_walked:,} files, {included:,} included"
    )

    skipped = [
        f"{count:,} {label}" for attr, label in _SKIP_REASONS if (count := getattr(stats, attr, 0))
    ]
    if skipped:
        progress.on_message("info", f"  Excluded: {', '.join(skipped)}")

    # Name the skipped *source* files. The aggregate line above cannot carry
    # this: "39 oversized" reads as images and lockfiles, so a dropped entry
    # point looks like nothing at all. That silence is what let a repo index
    # with its CLI, gateway and web server missing and nothing saying so
    # (#1237). Blobs stay in the aggregate — only files with a real parser
    # earn a name here.
    skipped_sources = getattr(stats, "skipped_source_files", [])
    if skipped_sources:
        # Local import: this module defers its ingestion imports so the phase
        # can be loaded without building the language registry.
        from repowise.core.ingestion.traverser import _SOURCE_MAX_FILE_SIZE_BYTES

        ceiling_kb = _SOURCE_MAX_FILE_SIZE_BYTES // 1024
        for skipped_source in skipped_sources:
            detail = {
                "minified": "looks minified",
                "unreadable": "could not be read",
            }.get(skipped_source.reason, f"over the {ceiling_kb:,} KB limit")
            progress.on_message(
                "warning",
                f"  Not indexed: {skipped_source.path} "
                f"({skipped_source.size_kb:,} KB, {detail})",
            )
        if getattr(stats, "skipped_source_files_truncated", False):
            progress.on_message("warning", "  ...and more source files skipped on size")

    if stats.lang_counts:
        ranked = sorted(stats.lang_counts.items(), key=lambda item: -item[1])
        lang_str = ", ".join(f"{lang} {count:,}" for lang, count in ranked[:_TOP_LANGUAGES_SHOWN])
        rest = sum(count for _, count in ranked[_TOP_LANGUAGES_SHOWN:])
        if rest:
            lang_str += f", other {rest:,}"
        progress.on_message("info", f"  Languages: {lang_str}")


# ---------------------------------------------------------------------------
# Process-pool worker (module-level — must be picklable)
# ---------------------------------------------------------------------------

# Force ``spawn`` for the parse pool instead of the POSIX default ``fork``.
# A workspace init generates docs for one repo (which imports and connects
# LanceDB) before ingesting the next, and ``lance`` is not fork-safe — it
# registers an ``os.register_at_fork`` hook that resets its native async
# runtime in the child but can still deadlock. Forking the parse workers
# after LanceDB is live is exactly what hung multi-repo inits at "Parsing
# files" (issue #678). ``spawn`` starts clean workers that never inherit the
# runtime; it is already the default on Windows/macOS, so ``_parse_one`` and
# its picklable args are known to work under it.
_MP_SPAWN = multiprocessing.get_context("spawn")

# Upper bound on the parse pool, independent of how many cores the host has.
#
# Every worker is a fresh interpreter under ``spawn`` that imports the repowise
# stack and lazily builds an ``ASTParser`` holding compiled tree-sitter
# Language/Query objects. That costs a flat ~50 MB of *private* memory per
# worker — measured at 51.0 MB/worker on PowerToys (5,766 files, 29.5 MB of
# source) and 49.2 MB/worker on hugo (2,130 files, 8.8 MB), so it is a
# per-worker constant and not a function of repo size. Sizing the pool from
# ``cpu_count`` therefore made peak memory a function of the *host*: 32 workers
# held 1.57 GB where 8 hold 0.46 GB.
#
# Nothing was bought with it. With 16 cores genuinely free, 8 workers parsed
# PowerToys in 5.54s/4.75s against 16 workers' 6.79s/5.23s (interleaved pairs):
# past this point the per-worker import and grammar build is paid again without
# amortizing over enough files to repay it, so more workers is slightly slower
# *and* multiplies the memory. The same reasoning caps the betweenness pool
# (see ``ingestion/graph/_betweenness.py``).
_MAX_PARSE_WORKERS = 8

# Escape hatch for hosts this default judges wrong in either direction.
_PARSE_WORKERS_ENV = "REPOWISE_PARSE_WORKERS"

# Module-level process-local parser cache (one per worker process).
_WORKER_PARSER: Any = None


def parse_pool_workers(pending: int) -> int:
    """How many workers to ask the parse pool for, to parse *pending* files.

    Stated once so the init and resume paths cannot drift apart, and so the
    bound can be asserted on directly. Never exceeds *pending* — a repo with
    three changed files has no use for eight interpreters.

    ``REPOWISE_PARSE_WORKERS`` overrides the cap in both directions. A set but
    non-positive value is ignored with a warning rather than failing an
    otherwise healthy parse; an empty value means "unset", which is what an
    ``export REPOWISE_PARSE_WORKERS=`` from an undefined shell variable
    produces, so it is not worth warning about.
    """
    if pending <= 0:
        return 1

    override = os.environ.get(_PARSE_WORKERS_ENV)
    if override:
        try:
            requested = int(override)
        except ValueError:
            requested = 0
        if requested > 0:
            return min(requested, pending)
        logger.warning("invalid_parse_workers_env", value=override)

    # ``process_cpu_count`` (3.13+) honours the CPU affinity mask, so a process
    # pinned to fewer CPUs than the host has stops sizing its pool from the
    # host's core count. It does *not* read cgroup CPU quotas: a container run
    # with ``--cpus=2`` (CFS quota, no cpuset) still sees the full host mask,
    # and the cap below is what bounds that case. Falls back to ``cpu_count``
    # on 3.11/3.12, and when affinity is unavailable and it returns None.
    affinity_aware = getattr(os, "process_cpu_count", None)
    cpus = (affinity_aware() if affinity_aware else None) or os.cpu_count() or 4
    return max(1, min(cpus, _MAX_PARSE_WORKERS, pending))


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


def _split_cached(
    repo_path: Path,
    fi_and_bytes: list[tuple],
    progress: ProgressCallback | None,
) -> tuple[Any, dict[int, Any], list[tuple[int, tuple, str]]]:
    """Partition pre-read files into parse-cache hits and misses.

    Returns ``(cache, hits, misses)`` where *hits* maps the position in
    *fi_and_bytes* to a cached ParsedFile and *misses* keeps ``(position,
    (FileInfo, bytes), content_hash)`` triples that still need a real parse
    (the hash rides along so the post-parse ``put`` doesn't re-digest).
    Positions let the caller reassemble results in traversal order. Hits
    tick the parse progress bar here (misses tick when their worker
    finishes). Cache failures degrade to all-miss.
    """
    from repowise.core.ingestion import compute_content_hash
    from repowise.core.ingestion.parse_cache import ParseCache

    cache = None
    hits: dict[int, Any] = {}
    misses: list[tuple[int, tuple, str]] = []
    try:
        cache = ParseCache(repo_path / ".repowise")
        cache.load()
    except Exception as exc:
        logger.debug("parse_cache_init_failed", error=str(exc))
        return None, hits, [(idx, item, "") for idx, item in enumerate(fi_and_bytes)]

    for idx, (fi, source) in enumerate(fi_and_bytes):
        content_hash = compute_content_hash(source)
        parsed = cache.get(fi, content_hash)
        if parsed is not None:
            hits[idx] = parsed
            if progress:
                progress.on_item_done("parse")
        else:
            misses.append((idx, (fi, source), content_hash))
    if hits:
        logger.info("parse_cache_used", hits=cache.hits, misses=cache.misses)
    return cache, hits, misses


def _cache_parsed(cache: Any, parsed: Any, content_hash: str) -> None:
    """Best-effort put of a freshly parsed file into the parse cache."""
    if cache is None or not content_hash:
        return
    # Cache failures must never fail the parse phase.
    with contextlib.suppress(Exception):
        cache.put(parsed, content_hash)


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
    derive_environment_facts: bool = False,
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
    # Results are kept in walk order, matching the update path's rebuild
    # (pipeline/incremental.py): files are fed to GraphBuilder in this order,
    # and ambiguous reference resolution tie-breaks on insertion order — a
    # completion-ordered list made the init-built graph differ from the
    # update-built one, so the first post-init update could never hit the
    # centrality cache. Progress still ticks per completion via callbacks.
    io_pool = ThreadPoolExecutor(max_workers=8)
    try:
        aws = [
            asyncio.wrap_future(io_pool.submit(traverser._build_file_info, p)) for p in all_paths
        ]
        if progress is not None:
            _traverse_tick = lambda _fut: progress.on_item_done("traverse")  # noqa: E731
            for fut in aws:
                fut.add_done_callback(_traverse_tick)
        ordered = await asyncio.gather(*aws, return_exceptions=True)
        file_infos: list[Any] = [
            r for r in ordered if r is not None and not isinstance(r, BaseException)
        ]
    finally:
        # shutdown(wait=True) is blocking — run in a thread to keep the
        # event loop responsive.  All submitted futures have already
        # completed by the time we reach here (gather awaited them).
        await asyncio.to_thread(io_pool.shutdown, wait=True)

    repo_structure = traverser.get_repo_structure(file_infos)

    # Structural episodes ride the walk that just finished — nested-repo names
    # and console scripts are by-products of it. The one check that describes
    # the machine rather than the repository is opt-in: this function is the
    # *full pipeline*, not ``init``, and the workspace updater and the hosted
    # job executor both reach it.
    await _record_structural_episodes(
        repo_path, traverser, allow_formatter_check=derive_environment_facts
    )
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

    # Report the traversal here, not at the end of the phase: the graph build,
    # dynamic hints, PageRank and community detection all run below, and on a
    # large repo "Scanned N files" used to land several minutes after the
    # traverse bar had finished and the user had moved on.
    _emit_traversal_summary(progress, traverser.stats, len(file_infos))

    # ---- Parse phase: CPU-bound, run in ProcessPoolExecutor ----------------
    # Say once, here, what each spawned worker would otherwise discover
    # independently and log at a level no default run renders: this
    # environment cannot parse some of the languages this repo contains.
    # Those files still index (paths, git history, regex-tier imports) but
    # carry no symbols, which is not something to find out from an empty
    # symbol list weeks later.
    _report_missing_grammars(getattr(traverser, "stats", None), progress)

    if progress:
        progress.on_phase_start("parse", len(file_infos))

    # Read source bytes up front in a thread pool (I/O-bound; keeps worker
    # args small: FileInfo + bytes, both picklable plain dataclasses/bytes).
    fi_and_bytes: list[tuple] = _read_sources(file_infos, progress)

    parsed_files: list[Any] = []
    source_map: dict[str, bytes] = {}
    from repowise.core.workspace.update import get_head_commit

    graph_builder = GraphBuilder(
        repo_path=repo_path,
        exclude_patterns=exclude_patterns,
        centrality_cache_dir=repo_path / ".repowise",
        head_commit=get_head_commit(repo_path),
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
    )

    loop = asyncio.get_running_loop()

    # Content-hash parse cache: unchanged files skip the worker pool
    # entirely (an incremental update re-ingests the whole repo for one
    # changed file). Misses parse exactly as before.
    parse_cache, cached_hits, to_parse = _split_cached(repo_path, fi_and_bytes, progress)
    workers = parse_pool_workers(len(to_parse))

    parse_results: list[Any] = []

    if to_parse:
        try:
            with ProcessPoolExecutor(max_workers=workers, mp_context=_MP_SPAWN) as pool:
                tasks = [
                    loop.run_in_executor(pool, _parse_one, item) for _idx, item, _h in to_parse
                ]
                # Tick the parse-progress bar as each worker finishes —
                # ``asyncio.gather`` would otherwise hold every event back
                # until the last file is done, which on PowerToys-scale
                # repos looked like a hang at ``0/N`` for many minutes.
                # Per-task done-callbacks fire on the event loop thread and
                # preserve gather's ordered results, so the aggregation
                # loop below still indexes ``to_parse`` correctly.
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
            # Worth saying out loud rather than only logging: the run still
            # produces a correct index, but every file is now parsed in one
            # process instead of up to eight, so a large repo takes several
            # times longer for a reason nothing on screen explained.
            emit_warning(
                progress,
                f"Parallel parsing unavailable ({pool_exc}); "
                "falling back to a single process, which is much slower.",
            )
            # Fallback: in-process sequential parse
            parse_results = []
            _fallback_parser = ASTParser()
            for i, (_idx, (fi, source), _h) in enumerate(to_parse):
                try:
                    result = _fallback_parser.parse_file(fi, source)
                    parse_results.append(result)
                except Exception as exc:
                    parse_results.append((fi.abs_path, str(exc)))
                if progress:
                    progress.on_item_done("parse")
                if i % 50 == 49:
                    await asyncio.sleep(0)

    # Merge fresh parses back into traversal order alongside cache hits.
    merged: dict[int, Any] = dict(cached_hits)
    for (idx, _item, content_hash), result in zip(to_parse, parse_results, strict=True):
        merged[idx] = result
        if not isinstance(result, tuple | Exception):
            _cache_parsed(parse_cache, result, content_hash)

    # Aggregate results into GraphBuilder on the main loop (not thread-safe).
    for idx in range(len(fi_and_bytes)):
        fi, source = fi_and_bytes[idx]
        result = merged.get(idx)
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], str):
            # Error tuple: (abs_path_str, error_str)
            logger.debug("parse_error_in_worker", path=result[0], error=result[1])
        elif isinstance(result, Exception):
            logger.debug("parse_exception_in_worker", path=fi.abs_path, error=str(result))
        elif result is not None:
            parsed_files.append(result)
            source_map[fi.path] = source
            graph_builder.add_file(result)
        # Process-pool path already ticked per-file via the done-callback
        # attached above; only the fallback path ticks here (handled in
        # its own loop). No tick needed in aggregation.

    if parse_cache is not None:
        parse_cache.save()
    # Lend the just-read bytes to the builder so the language warmups that
    # scan file text during build() don't re-read the repo.
    graph_builder.set_source_map(source_map)
    _phase_done(progress, "parse")

    # ---- tsconfig path-alias resolver (before graph build) ------------------
    # Only runs when the repo has TS/JS files. On large TS monorepos the
    # resolver indexes hundreds of tsconfig files up-front; without a phase
    # label this shows up as a silent gap right after parsing.
    _ts_langs = {"typescript", "javascript", "svelte", "vue"}
    if any(pf.file_info.language in _ts_langs for pf in parsed_files):
        if progress:
            progress.on_phase_start("tsconfig", None)
        from repowise.core.ingestion import wire_tsconfig_resolver

        wire_tsconfig_resolver(
            graph_builder,
            repo_path,
            include_submodules=include_submodules,
            include_nested_repos=include_nested_repos,
        )
        _phase_done(progress, "tsconfig")

    # ---- Graph build phase -------------------------------------------------
    # Sub-phases (graph.imports / graph.heritage / graph.calls) are emitted
    # from inside GraphBuilder.build(); the orchestrator drives metrics/
    # communities/flows below so the longest-running step is no longer an
    # opaque "graph 0/1" spinner.
    if progress and len(file_infos) >= _SLOW_GRAPH_BUILD_FILES:
        # Reassurance before the longest silent stretch of the run, so it is
        # warning-weight rather than another dim stat — which is also why it is
        # gated: on a small repo the build is over in under a second, and a
        # yellow "this may take several minutes" there is a false alarm made
        # louder.
        progress.on_message(
            "warning",
            "Graph build can take several minutes on a first run. Safe to Ctrl-C — "
            "re-run 'repowise init --resume' to continue where it stopped.",
        )
    await asyncio.to_thread(graph_builder.build, progress)

    # Add framework-aware synthetic edges (conftest, Django, FastAPI, Flask).
    # Two failures with two different costs, reported separately rather than as
    # one silent pass: the stack also feeds the persisted tech list and the
    # editor files, so losing it costs more than losing the edges. Keeping the
    # edge call outside its try is deliberate — the handlers that read no stack
    # (conftest, gtest, Flutter, Android) still run when detection failed.
    tech_items: list = []
    try:
        from repowise.core.generation.editor_files.tech_stack import detect_tech_stack

        tech_items = detect_tech_stack(repo_path)
    except Exception as tech_exc:
        logger.warning("tech_stack_detection_failed", error=str(tech_exc))
        emit_warning(
            progress,
            f"Tech stack detection skipped ({tech_exc}); the repo's frameworks will be "
            "missing from the index, along with every edge that depends on them.",
        )

    try:
        graph_builder.add_framework_edges([item.name for item in tech_items])
    except Exception as fw_exc:
        logger.warning("framework_edges_failed", error=str(fw_exc))
        emit_warning(
            progress,
            f"Framework edge detection skipped ({fw_exc}); framework-invoked "
            "symbols will have no callers in the graph and may read as dead.",
        )

    # ---- Dynamic hints wiring (after static graph is fully built) ----------
    if progress:
        progress.on_phase_start("dynamic_hints", None)
    try:
        from repowise.core.ingestion.dynamic_hints import HintRegistry

        registry = HintRegistry()
        # Feed the traversed file list so hints query the indexed set
        # directly instead of re-walking the tree (keeps gitignored and
        # generated files out of hint edges, and skips a full walk).
        hint_paths = [fi.path for fi in file_infos]
        dynamic_edges = await loop.run_in_executor(
            None,
            lambda: registry.extract_all(
                repo_path, dotnet_index=graph_builder.dotnet_index, file_paths=hint_paths
            ),
        )
        graph_builder.add_dynamic_edges(dynamic_edges)
        logger.info("dynamic_hints_added", count=len(dynamic_edges))
    except Exception as hints_exc:
        logger.warning("dynamic_hints_failed", error=str(hints_exc))
        emit_warning(
            progress,
            f"Dynamic edge hints skipped ({hints_exc}); "
            "framework-wired call edges will be missing from the graph.",
        )
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

    return (
        parsed_files,
        file_infos,
        repo_structure,
        source_map,
        graph_builder,
        traverser.stats,
        tech_items,
    )


async def reparse_for_resume(
    repo_path: Path,
    *,
    exclude_patterns: list[str] | None,
    include_submodules: bool = False,
    include_nested_repos: bool = False,
    skip_tests: bool,
    skip_infra: bool,
    progress: ProgressCallback | None,
    derive_environment_facts: bool = False,
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
    # A resumed run is still the same command: the interrupted one may never
    # have reached the derivation, so it runs here too rather than leaving a
    # resumed repo without the facts a fresh one gets.
    await _record_structural_episodes(
        repo_path, traverser, allow_formatter_check=derive_environment_facts
    )
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

    parse_cache, cached_hits, to_parse = _split_cached(repo_path, fi_and_bytes, progress)
    workers = parse_pool_workers(len(to_parse))
    parse_results: list[Any] = []

    if to_parse:
        try:
            with ProcessPoolExecutor(max_workers=workers, mp_context=_MP_SPAWN) as pool:
                tasks = [
                    loop.run_in_executor(pool, _parse_one, item) for _idx, item, _h in to_parse
                ]
                if progress is not None:
                    _tick = lambda _fut: progress.on_item_done("parse")  # noqa: E731
                    for fut in tasks:
                        fut.add_done_callback(_tick)
                parse_results = await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as pool_exc:
            logger.warning("resume_reparse_pool_failed_falling_back", error=str(pool_exc))
            parse_results = []
            _fallback_parser = ASTParser()
            for i, (_idx, (fi, source), _h) in enumerate(to_parse):
                try:
                    parse_results.append(_fallback_parser.parse_file(fi, source))
                except Exception as exc:
                    parse_results.append((fi.abs_path, str(exc)))
                if progress:
                    progress.on_item_done("parse")
                if i % 50 == 49:
                    await asyncio.sleep(0)

    merged: dict[int, Any] = dict(cached_hits)
    for (idx, _item, content_hash), result in zip(to_parse, parse_results, strict=True):
        merged[idx] = result
        if not isinstance(result, tuple | Exception):
            _cache_parsed(parse_cache, result, content_hash)

    for idx in range(len(fi_and_bytes)):
        fi, source = fi_and_bytes[idx]
        result = merged.get(idx)
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], str):
            logger.debug("parse_error_in_worker", path=result[0], error=result[1])
        elif isinstance(result, Exception):
            logger.debug("parse_exception_in_worker", path=fi.abs_path, error=str(result))
        elif result is not None:
            parsed_files.append(result)
            source_map[fi.path] = source

    if parse_cache is not None:
        parse_cache.save()
    _phase_done(progress, "parse")

    tech_items: list = []
    try:
        from repowise.core.generation.editor_files.tech_stack import detect_tech_stack

        tech_items = detect_tech_stack(repo_path)
    except Exception as tech_exc:
        logger.warning("tech_stack_detection_failed", error=str(tech_exc))
        emit_warning(
            progress,
            f"Tech stack detection skipped ({tech_exc}); the repo's frameworks will be "
            "missing from the index.",
        )

    return parsed_files, file_infos, repo_structure, source_map, tech_items
