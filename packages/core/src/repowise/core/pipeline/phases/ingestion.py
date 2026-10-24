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
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from repowise.core.pipeline.progress import ProgressCallback, emit_item, emit_warning

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
        from repowise.core.ingestion.languages.registry import REGISTRY
        from repowise.core.ingestion.parser import (
            missing_grammar_languages,
            unavailable_runtime_grammars,
        )

        missing = missing_grammar_languages(lang_counts)
        unavailable = unavailable_runtime_grammars(lang_counts)
    except Exception:
        logger.debug("grammar_preflight_failed", exc_info=True)
        return
    if missing:
        detail = ", ".join(f"{lang} ({lang_counts[lang]:,} files)" for lang in missing)
        emit_warning(
            progress,
            f"No parser installed for: {detail}. "
            "These files are indexed but contribute no symbols; "
            "reinstall repowise to pick up the missing grammars.",
        )
    for lang, reason in unavailable:
        spec = REGISTRY.get(lang)
        emit_warning(
            progress,
            f"{spec.display_name if spec else lang} grammar unavailable: "
            f"{lang_counts[lang]:,} files indexed without symbols ({reason}). "
            "It downloads on first use; on a machine with no network, set "
            "REPOWISE_GRAMMAR_DIR to a pre-staged grammar cache.",
        )


def _report_query_compilation_failures(stats: Any, progress: ProgressCallback | None) -> None:
    """Name any languages whose tree-sitter queries failed to compile.

    Scoped to what traversal found. A query compilation failure (e.g. tree-sitter
    ABI mismatch or invalid query syntax) leaves files with zero symbols.
    Reporting it prominently tells the user why symbols are missing. Best-effort:
    a reporting failure must not stop an index.
    """
    if progress is None or stats is None:
        return
    try:
        lang_counts = getattr(stats, "lang_counts", None)
        if not lang_counts:
            return
        from repowise.core.ingestion.parser import failed_query_languages

        failed = failed_query_languages(lang_counts)
        if not failed:
            return
        for lang, err in failed:
            count = lang_counts.get(lang, 0)
            emit_warning(
                progress,
                f"Failed to compile tree-sitter queries for {lang} ({count:,} files): {err}. "
                f"Symbols for {lang} will not be extracted.",
            )
    except Exception:
        logger.debug("query_preflight_failed", exc_info=True)
        return


async def _run_concurrent_steps(
    phase: str,
    steps: list[tuple[str, Any]],
    progress: ProgressCallback | None,
) -> None:
    """Run each ``(label, fn)`` in a worker thread at once, as one counted phase.

    The running row's count advances as each finishes and its current item
    names the ones still running, so a long kernel (symbol betweenness on a
    big repo) is visibly the one holding the step. Per-step times go to the
    log rather than the screen: one done row per phase, no extra lines.
    """
    running = [label for label, _ in steps]
    if progress:
        progress.on_phase_start(phase, len(steps))
    emit_item(progress, phase, ", ".join(running))

    async def _one(label: str, fn: Any) -> None:
        t0 = time.monotonic()
        try:
            await asyncio.to_thread(fn)
        except Exception as exc:
            if progress is not None:
                progress.on_message(
                    "warning",
                    f"  {label} failed after {time.monotonic() - t0:.1f}s: {exc}",
                )
            raise
        logger.info("graph_step_done", step=label, seconds=round(time.monotonic() - t0, 1))
        running.remove(label)
        if progress:
            progress.on_item_done(phase)
        emit_item(progress, phase, ", ".join(running))

    await asyncio.gather(*(_one(label, fn) for label, fn in steps))
    _phase_done(progress, phase)


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
    ("skipped_repowise_output", "written by repowise"),
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
                "notice",
                f"  Not indexed: {skipped_source.path} "
                f"({skipped_source.size_kb:,} KB, {detail})",
            )
        if getattr(stats, "skipped_source_files_truncated", False):
            progress.on_message("notice", "  ...and more source files skipped on size")

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
            from repowise.core.worker_logging import quiet_worker_logs

            quiet_worker_logs()

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


# Languages ``skip_infra`` drops from the parse set.
_INFRA_LANGUAGES = ("dockerfile", "makefile", "terraform", "shell")

# TS/JS-family languages whose presence wires the tsconfig path-alias resolver.
_TS_LANGUAGES = frozenset({"typescript", "javascript", "svelte", "vue", "astro"})


@dataclass(frozen=True)
class _WalkOptions:
    """What to walk and keep, shared by the full and the resume ingestion."""

    exclude_patterns: list[str] | None
    include_submodules: bool
    include_nested_repos: bool
    skip_tests: bool
    skip_infra: bool
    derive_environment_facts: bool


async def _build_file_infos(traverser: Any, progress: ProgressCallback | None) -> list[Any]:
    """Walk the tree and build a FileInfo per path, in walk order.

    Stat + header reads run in a thread pool; ``asyncio.wrap_future`` keeps the
    event loop responsive while they run. Results stay in walk order, matching
    the update path's rebuild (pipeline/incremental.py): files reach
    GraphBuilder in this order and ambiguous reference resolution tie-breaks on
    insertion order. Progress still ticks per completion.
    """
    all_paths = list(traverser._walk())
    if progress:
        # Indeterminate (spinner): a pre-filter total like "2132/83601" misleads.
        progress.on_phase_start("traverse", None)

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
        return [r for r in ordered if r is not None and not isinstance(r, BaseException)]
    finally:
        # shutdown(wait=True) blocks; every future is already done (gather
        # awaited them), so this only keeps the event loop responsive.
        await asyncio.to_thread(io_pool.shutdown, wait=True)


def _filter_file_infos(file_infos: list[Any], walk: _WalkOptions) -> list[Any]:
    if walk.skip_tests:
        file_infos = [fi for fi in file_infos if not fi.is_test]
    if walk.skip_infra:
        file_infos = [fi for fi in file_infos if fi.language not in _INFRA_LANGUAGES]
    return file_infos


async def _traverse_stage(
    repo_path: Path,
    walk: _WalkOptions,
    progress: ProgressCallback | None,
) -> tuple[Any, list[Any], Any]:
    """Walk, describe the repo, record structural episodes, then filter.

    Returns ``(traverser, file_infos, repo_structure)``; *file_infos* is in
    walk order with ``skip_tests`` / ``skip_infra`` applied, while
    *repo_structure* describes the unfiltered walk.
    """
    from repowise.core.ingestion import FileTraverser

    traverser = FileTraverser(
        repo_path,
        extra_exclude_patterns=walk.exclude_patterns or None,
        include_submodules=walk.include_submodules,
        include_nested_repos=walk.include_nested_repos,
    )
    file_infos = await _build_file_infos(traverser, progress)
    repo_structure = traverser.get_repo_structure(file_infos)

    # Structural episodes ride the walk that just finished: nested-repo names
    # and console scripts are by-products of it. The one check that describes
    # the machine rather than the repository is opt-in, because the full
    # pipeline is also reached by the workspace updater and hosted jobs.
    await _record_structural_episodes(
        repo_path, traverser, allow_formatter_check=walk.derive_environment_facts
    )
    _phase_done(progress, "traverse")
    file_infos = _filter_file_infos(file_infos, walk)
    # The threaded walk skips ``traverse()``, which is what counts languages.
    traverser.stats.lang_counts = dict(Counter(f.language for f in file_infos))
    return traverser, file_infos, repo_structure


async def _parse_in_pool(
    to_parse: list[tuple], workers: int, progress: ProgressCallback | None
) -> list[Any]:
    """Parse cache misses in the spawn process pool; results in *to_parse* order."""
    loop = asyncio.get_running_loop()
    with ProcessPoolExecutor(max_workers=workers, mp_context=_MP_SPAWN) as pool:
        tasks = [loop.run_in_executor(pool, _parse_one, item) for _idx, item, _h in to_parse]
        # Tick per finished worker: ``asyncio.gather`` alone holds every event
        # until the last file is done, which on large repos looked like a hang
        # at ``0/N``. Done-callbacks keep gather's ordered results intact.
        if progress is not None:

            def _parse_tick(path: str) -> Any:
                def _tick(_fut: Any) -> None:
                    progress.on_item_done("parse")
                    emit_item(progress, "parse", path)

                return _tick

            for fut, (_idx, (fi, _src), _h) in zip(tasks, to_parse, strict=True):
                fut.add_done_callback(_parse_tick(fi.path))
        return await asyncio.gather(*tasks, return_exceptions=True)


async def _parse_in_process(to_parse: list[tuple], progress: ProgressCallback | None) -> list[Any]:
    """Sequential in-process parse, the fallback when the pool is unavailable."""
    from repowise.core.ingestion import ASTParser

    parse_results: list[Any] = []
    fallback_parser = ASTParser()
    for i, (_idx, (fi, source), _h) in enumerate(to_parse):
        try:
            parse_results.append(fallback_parser.parse_file(fi, source))
        except Exception as exc:
            parse_results.append((fi.abs_path, str(exc)))
        if progress:
            progress.on_item_done("parse")
            emit_item(progress, "parse", fi.path)
        if i % 50 == 49:
            await asyncio.sleep(0)
    return parse_results


async def _parse_misses(
    to_parse: list[tuple],
    progress: ProgressCallback | None,
    *,
    fallback_event: str,
    warn_on_fallback: bool,
) -> list[Any]:
    workers = parse_pool_workers(len(to_parse))
    if not to_parse:
        return []
    try:
        return await _parse_in_pool(to_parse, workers, progress)
    except Exception as pool_exc:
        logger.warning(fallback_event, error=str(pool_exc))
        if warn_on_fallback:
            # Said out loud, not only logged: the index is still correct, but
            # one process instead of up to eight makes a large repo several
            # times slower for a reason nothing on screen explained.
            emit_warning(
                progress,
                f"Parallel parsing unavailable ({pool_exc}); "
                "falling back to a single process, which is much slower.",
            )
        return await _parse_in_process(to_parse, progress)


def _merge_parse_results(
    cached_hits: dict[int, Any],
    to_parse: list[tuple],
    parse_results: list[Any],
    parse_cache: Any,
) -> dict[int, Any]:
    """Key fresh parses by traversal position next to the cache hits."""
    merged: dict[int, Any] = dict(cached_hits)
    for (idx, _item, content_hash), result in zip(to_parse, parse_results, strict=True):
        merged[idx] = result
        if not isinstance(result, tuple | Exception):
            _cache_parsed(parse_cache, result, content_hash)
    return merged


def _collect_parsed(
    fi_and_bytes: list[tuple],
    merged: dict[int, Any],
) -> tuple[list[Any], dict[str, bytes]]:
    """Successful parses and their source bytes, in traversal order."""
    parsed_files: list[Any] = []
    source_map: dict[str, bytes] = {}
    for idx, (fi, source) in enumerate(fi_and_bytes):
        result = merged.get(idx)
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], str):
            # Error tuple from a worker: (abs_path_str, error_str)
            logger.debug("parse_error_in_worker", path=result[0], error=result[1])
        elif isinstance(result, Exception):
            logger.debug("parse_exception_in_worker", path=fi.abs_path, error=str(result))
        elif result is not None:
            parsed_files.append(result)
            source_map[fi.path] = source
    return parsed_files, source_map


async def _parse_stage(
    repo_path: Path,
    file_infos: list[Any],
    progress: ProgressCallback | None,
    *,
    fallback_event: str,
    warn_on_fallback: bool,
) -> tuple[list[Any], dict[str, bytes]]:
    """Read, cache-split and parse *file_infos*; results keep their order.

    Source bytes are read up front in a thread pool so worker args stay small
    (FileInfo + bytes). Content-hash cache hits skip the worker pool; misses
    are parsed in the process pool. Does not mark the parse phase done.
    """
    if progress:
        progress.on_phase_start("parse", len(file_infos))
    fi_and_bytes: list[tuple] = _read_sources(file_infos, progress)
    parse_cache, cached_hits, to_parse = _split_cached(repo_path, fi_and_bytes, progress)
    parse_results = await _parse_misses(
        to_parse, progress, fallback_event=fallback_event, warn_on_fallback=warn_on_fallback
    )
    merged = _merge_parse_results(cached_hits, to_parse, parse_results, parse_cache)
    parsed_files, source_map = _collect_parsed(fi_and_bytes, merged)
    if parse_cache is not None:
        parse_cache.save()
    return parsed_files, source_map


def _detect_tech_items(
    repo_path: Path, progress: ProgressCallback | None, *, consequence: str
) -> list:
    """Detect the repo's tech stack; a failure warns and yields ``[]``."""
    try:
        from repowise.core.generation.editor_files.tech_stack import detect_tech_stack

        return detect_tech_stack(repo_path)
    except Exception as tech_exc:
        logger.warning("tech_stack_detection_failed", error=str(tech_exc))
        emit_warning(
            progress,
            f"Tech stack detection skipped ({tech_exc}); the repo's frameworks will be "
            f"missing from the index{consequence}",
        )
        return []


def _new_graph_builder(
    repo_path: Path,
    traverser: Any,
    parsed_files: list[Any],
    source_map: dict[str, bytes],
    walk: _WalkOptions,
) -> Any:
    """A GraphBuilder fed *parsed_files* in order (insertion order breaks ties)."""
    from repowise.core.ingestion import GraphBuilder
    from repowise.core.workspace.update import get_head_commit

    graph_builder = GraphBuilder(
        repo_path=repo_path,
        exclude_patterns=walk.exclude_patterns,
        centrality_cache_dir=repo_path / ".repowise",
        head_commit=get_head_commit(repo_path),
        include_submodules=walk.include_submodules,
        include_nested_repos=walk.include_nested_repos,
    )
    graph_builder.console_scripts = traverser.console_script_names
    for parsed in parsed_files:
        graph_builder.add_file(parsed)
    # Lend the just-read bytes so the language warmups that scan file text
    # during build() don't re-read the repo.
    graph_builder.set_source_map(source_map)
    graph_builder.set_pytest_roots(traverser.pytest_roots)
    return graph_builder


def _wire_tsconfig(
    graph_builder: Any,
    repo_path: Path,
    parsed_files: list[Any],
    walk: _WalkOptions,
    progress: ProgressCallback | None,
) -> None:
    """Wire the tsconfig path-alias resolver when the repo has TS/JS files.

    Its own phase label: on large TS monorepos it indexes hundreds of tsconfig
    files, which otherwise shows as a silent gap right after parsing.
    """
    if not any(pf.file_info.language in _TS_LANGUAGES for pf in parsed_files):
        return
    if progress:
        progress.on_phase_start("tsconfig", None)
    from repowise.core.ingestion import wire_tsconfig_resolver

    wire_tsconfig_resolver(
        graph_builder,
        repo_path,
        include_submodules=walk.include_submodules,
        include_nested_repos=walk.include_nested_repos,
    )
    _phase_done(progress, "tsconfig")


async def _build_graph(
    graph_builder: Any, file_count: int, progress: ProgressCallback | None
) -> None:
    """Run ``GraphBuilder.build`` (it emits graph.imports/heritage/calls itself)."""
    if progress and file_count >= _SLOW_GRAPH_BUILD_FILES:
        # Reassurance before the longest silent stretch, as a "notice": not a
        # degradation, so never persisted into state.json["degraded"]. Gated
        # because on a small repo the build is over in under a second.
        progress.on_message(
            "notice",
            "Graph build can take several minutes on a first run. Safe to Ctrl-C: "
            "re-run 'repowise init --resume' to continue where it stopped.",
        )
    await asyncio.to_thread(graph_builder.build, progress)


def _add_framework_edges(
    graph_builder: Any, tech_items: list, progress: ProgressCallback | None
) -> None:
    """Framework-aware synthetic edges (conftest, Django, FastAPI, Flask).

    Runs even when stack detection failed: the handlers that read no stack
    (conftest, gtest, Flutter, Android) still apply.
    """
    try:
        graph_builder.add_framework_edges([item.name for item in tech_items])
    except Exception as fw_exc:
        logger.warning("framework_edges_failed", error=str(fw_exc))
        emit_warning(
            progress,
            f"Framework edge detection skipped ({fw_exc}); framework-invoked "
            "symbols will have no callers in the graph and may read as dead.",
        )


async def _add_dynamic_hints(
    graph_builder: Any,
    repo_path: Path,
    file_infos: list[Any],
    source_map: dict[str, bytes],
    progress: ProgressCallback | None,
) -> None:
    """Dynamic hint edges, after the static graph is fully built."""
    if progress:
        progress.on_phase_start("dynamic_hints", None)
    try:
        from repowise.core.ingestion.dynamic_hints import HintRegistry

        registry = HintRegistry()
        # The traversed file list keeps gitignored and generated files out of
        # hint edges and skips a second walk.
        hint_paths = [fi.path for fi in file_infos]
        loop = asyncio.get_running_loop()
        dynamic_edges = await loop.run_in_executor(
            None,
            lambda: registry.extract_all(
                repo_path,
                dotnet_index=graph_builder.dotnet_index,
                file_paths=hint_paths,
                source_map=source_map,
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


async def _prime_graph_metrics(graph_builder: Any, progress: ProgressCallback | None) -> None:
    """Compute centrality and communities now, each as a visible sub-phase.

    Otherwise they run lazily inside persist and generation behind opaque
    bars. Within a phase the kernels overlap via ``asyncio.gather`` so
    betweenness (the dominant cost) runs alongside the rest, and the
    running row names the kernels still going, so it names the bottleneck.
    """
    await _run_concurrent_steps(
        "graph.metrics",
        [
            ("PageRank", graph_builder.pagerank),
            ("betweenness centrality", graph_builder.betweenness_centrality),
            ("symbol PageRank", graph_builder.symbol_pagerank),
            ("symbol betweenness", graph_builder.symbol_betweenness_centrality),
        ],
        progress,
    )
    await _run_concurrent_steps(
        "graph.communities",
        [
            ("community detection", graph_builder.community_detection),
            ("symbol communities", graph_builder.symbol_communities),
        ],
        progress,
    )


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
    walk = _WalkOptions(
        exclude_patterns=exclude_patterns,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
        skip_tests=skip_tests,
        skip_infra=skip_infra,
        derive_environment_facts=derive_environment_facts,
    )
    traverser, file_infos, repo_structure = await _traverse_stage(repo_path, walk, progress)

    # Reported now rather than at the end of the phase: the graph build and
    # metrics below can take minutes on a large repo.
    _emit_traversal_summary(progress, traverser.stats, len(file_infos))

    # Said once here rather than by every worker at a level no default run
    # renders: files in these languages index without symbols.
    _report_missing_grammars(getattr(traverser, "stats", None), progress)
    _report_query_compilation_failures(getattr(traverser, "stats", None), progress)

    parsed_files, source_map = await _parse_stage(
        repo_path,
        file_infos,
        progress,
        fallback_event="process_pool_parse_failed_falling_back",
        warn_on_fallback=True,
    )
    graph_builder = _new_graph_builder(repo_path, traverser, parsed_files, source_map, walk)
    _phase_done(progress, "parse")

    _wire_tsconfig(graph_builder, repo_path, parsed_files, walk, progress)
    await _build_graph(graph_builder, len(file_infos), progress)
    # Stack detection and framework edges fail separately: the stack also
    # feeds the persisted tech list and the editor files.
    tech_items = _detect_tech_items(
        repo_path, progress, consequence=", along with every edge that depends on them."
    )
    _add_framework_edges(graph_builder, tech_items, progress)
    await _add_dynamic_hints(graph_builder, repo_path, file_infos, source_map, progress)
    await _prime_graph_metrics(graph_builder, progress)

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
    walk = _WalkOptions(
        exclude_patterns=exclude_patterns,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
        skip_tests=skip_tests,
        skip_infra=skip_infra,
        derive_environment_facts=derive_environment_facts,
    )
    _traverser, file_infos, repo_structure = await _traverse_stage(repo_path, walk, progress)
    parsed_files, source_map = await _parse_stage(
        repo_path,
        file_infos,
        progress,
        fallback_event="resume_reparse_pool_failed_falling_back",
        warn_on_fallback=False,
    )
    _phase_done(progress, "parse")
    tech_items = _detect_tech_items(repo_path, progress, consequence=".")
    return parsed_files, file_infos, repo_structure, source_map, tech_items
