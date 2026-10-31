"""Rebuild the repo graph and re-index git metadata for the changed files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repowise.core.pipeline.phase_timing import PhaseTimings, timed

from ._common import LogFn, _noop_log, logger


def build_filtered_changed_paths(file_diffs: list, exclude_patterns: list[str]) -> list[str]:
    """Extract paths from file_diffs, filtering out excluded patterns."""
    paths = [fd.path for fd in file_diffs]
    if not exclude_patterns:
        return paths
    import pathspec

    spec = pathspec.PathSpec.from_lines("gitwildmatch", exclude_patterns)
    return [p for p in paths if not spec.match_file(p)]


def _record_structural_episodes(repo_path: Any, traverser: Any) -> None:
    """Best-effort structural episodes from the walk, without the formatter check."""
    try:
        from repowise.core.precedent.structural import record_structural_episodes

        record_structural_episodes(repo_path, traverser, allow_formatter_check=False)
    except Exception:
        pass


def _parse_misses(
    cached_hits: dict[int, Any],
    to_parse: list,
    parse_cache: Any,
    timings: PhaseTimings | None,
    log: LogFn = _noop_log,
) -> dict[int, Any]:
    """Parse the parse-cache misses and merge them over the cached hits by index.

    Misses go through init's deadline pool, so one file that never finishes
    parsing costs this update one deadline instead of hanging it. An update
    whose files are all cached starts no pool.
    """
    from repowise.core.pipeline.phases.ingestion import (
        _cache_parsed,
        parse_guarded,
        report_parse_failures,
    )

    merged: dict[int, Any] = dict(cached_hits)
    with timed(timings, "rebuild.parse"):
        results = parse_guarded(to_parse)
    for (idx, _item, content_hash), parsed in zip(to_parse, results, strict=True):
        if isinstance(parsed, tuple | BaseException):
            continue
        parsed.content_hash = content_hash
        merged[idx] = parsed
        if content_hash:
            _cache_parsed(parse_cache, parsed, content_hash)
    message = report_parse_failures(to_parse, results)
    if message:
        log(f"[yellow]{message}[/yellow]")
    return merged


def _add_parsed_files(
    graph_builder: Any,
    fi_and_bytes: list,
    merged: dict[int, Any],
    collect_sources: bool,
) -> tuple[list, dict[str, bytes], int]:
    """Add every parsed file to the graph, in read order.

    Returns ``(parsed_files, source_map, unparsed)``, where ``unparsed`` counts
    the read files that have no parse result.
    """
    parsed_files: list = []
    source_map: dict[str, bytes] = {}
    unparsed = 0
    for idx, (fi, source) in enumerate(fi_and_bytes):
        parsed = merged.get(idx)
        if parsed is None:
            unparsed += 1
            continue
        parsed_files.append(parsed)
        if collect_sources:
            source_map[fi.path] = source
        graph_builder.add_file(parsed)
    return parsed_files, source_map, unparsed


def _add_synthetic_edges(
    repo_path: Any,
    graph_builder: Any,
    file_infos: list,
    source_map: dict[str, bytes],
    log: LogFn,
    timings: PhaseTimings | None,
) -> None:
    """Add the framework-aware and dynamic-hint edges to a built graph."""
    # Add framework-aware synthetic edges (conftest, Django, FastAPI, Flask).
    # Best-effort, but not silent: the update path has to report the failure the
    # init path reports, or an index degrades differently depending on how it
    # was built and nothing says which.
    try:
        from repowise.core.generation.editor_files.tech_stack import detect_tech_stack

        with timed(timings, "rebuild.framework"):
            tech_items = detect_tech_stack(repo_path)
            fw_count = graph_builder.add_framework_edges([item.name for item in tech_items])
        if fw_count:
            log(f"Framework edges added: [cyan]{fw_count}[/cyan]")
    except Exception as fw_exc:
        logger.warning("framework_edges_failed", error=str(fw_exc))
        log(
            f"[yellow]Framework edge detection skipped: {fw_exc}; framework-invoked "
            "symbols will have no callers in the graph and may read as dead.[/yellow]"
        )

    # Add dynamic-hint edges, mirroring the init pipeline's ingestion phase.
    # Without this the update-built graph was missing every dynamic edge the
    # init graph had: metrics computed on update diverged from init's, and
    # the first post-init update could never hit the centrality cache.
    try:
        from repowise.core.ingestion.dynamic_hints import HintRegistry

        with timed(timings, "rebuild.dynamic_hints"):
            dynamic_edges = HintRegistry().extract_all(
                Path(repo_path),
                dotnet_index=graph_builder.dotnet_index,
                file_paths=[fi.path for fi in file_infos],
                source_map=source_map or None,
            )
            graph_builder.add_dynamic_edges(dynamic_edges)
        if dynamic_edges:
            log(f"Dynamic hint edges added: [cyan]{len(dynamic_edges)}[/cyan]")

    except Exception as exc:
        logger.warning("dynamic_hints_extraction_failed", error=str(exc))
        log(
            f"[yellow]Dynamic hint extraction failed: {exc}. Dynamic edges"
            "will be missing from this update.[/yellow]"
        )


def _plan_graph_patch(
    repo_path: Any,
    *,
    patch_requested: bool,
    seed_edges: Any | None,
    parse_cache: Any,
    parsed_files: list[Any],
    merged: dict[int, Any],
    to_parse: list[tuple[int, tuple, str]],
    changed_paths: set[str] | None,
) -> tuple[Any, dict[str, tuple] | None]:
    """Return the safe patch plan and restored-import cache, if eligible."""
    from .surface import PatchPlan, plan_patch

    if not patch_requested:
        return PatchPlan(False, reason="feature disabled"), None
    if seed_edges is None:
        return PatchPlan(False, reason="no valid persisted graph snapshot"), None

    changed = {
        fi.path: merged[idx] for idx, (fi, _source), _hash in to_parse if idx in merged
    }
    detected_changes = set(changed) if changed_paths is None else changed_paths
    if detected_changes != set(changed):
        return (
            PatchPlan(
                False,
                reason="detected changes include unparsed files or do not match the parse cache",
            ),
            None,
        )

    patch_plan = plan_patch(
        parse_cache=parse_cache,
        current_paths=[pf.file_info.path for pf in parsed_files],
        changed=changed,
    )
    if not patch_plan.eligible:
        return patch_plan, None

    from .resolution_cache import covers, load_resolutions

    resolutions = load_resolutions(Path(repo_path) / ".repowise")
    if covers(resolutions, parsed_files, patch_plan.scope):
        return patch_plan, resolutions

    missing_path = next(
        (
            pf.file_info.path
            for pf in parsed_files
            if pf.file_info.path not in patch_plan.scope
            and (
                resolutions is None
                or pf.file_info.path not in resolutions
                or resolutions[pf.file_info.path][0] != pf.content_hash
            )
        ),
        None,
    )
    reason = "saved import resolutions do not cover every unchanged file"
    if missing_path:
        reason = f"{missing_path}: {reason}"
    return PatchPlan(False, reason=reason), None


def _wire_graph_builder(
    graph_builder: Any,
    repo_path: Any,
    *,
    include_submodules: bool,
    include_nested_repos: bool,
    source_map: dict[str, bytes],
    pytest_roots: Any,
) -> None:
    """Attach repo-wide resolvers and source context needed by either build path."""
    from repowise.core.ingestion import wire_tsconfig_resolver

    wire_tsconfig_resolver(
        graph_builder,
        repo_path,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
    )
    graph_builder.set_source_map(source_map)
    graph_builder.set_pytest_roots(pytest_roots)


def _build_graph_with_plan(
    graph_builder: Any,
    *,
    patch_plan: Any,
    resolutions: dict[str, tuple] | None,
    parsed_files: list[Any],
    seed_edges: Any | None,
    timings: PhaseTimings | None,
    log: LogFn,
) -> None:
    """Run the scoped or full resolver build and record its specific timing."""
    if patch_plan.eligible:
        from .resolution_cache import restore_resolutions

        with timed(timings, "rebuild.graph.patch"):
            restore_resolutions(resolutions, parsed_files, patch_plan.scope)
            seeded = graph_builder.seed_graph_snapshot(seed_edges)
            log(
                f"Patching the graph: re-resolving {len(patch_plan.scope)} file(s), "
                f"reusing {seeded:,} edges"
            )
            graph_builder.build(scope=patch_plan.scope)
        return

    with timed(timings, "rebuild.graph.full"):
        if seed_edges is not None:
            log(f"[yellow]Full graph rebuild: {patch_plan.reason}[/yellow]")
        graph_builder.build()


def _traverse_repo(
    repo_path: Any,
    exclude_patterns: list[str],
    *,
    include_submodules: bool,
    include_nested_repos: bool,
    timings: PhaseTimings | None,
) -> tuple[Any, list[Any], Any]:
    """Walk once, build file metadata in parallel, and derive repo structure."""
    import os
    from concurrent.futures import ThreadPoolExecutor

    from repowise.core.ingestion import FileTraverser

    traverser = FileTraverser(
        repo_path,
        extra_exclude_patterns=exclude_patterns or None,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
    )
    with timed(timings, "rebuild.traverse"):
        all_paths = list(traverser._walk())
        workers = min(32, max(4, (os.cpu_count() or 4) * 2))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            maybe_infos = list(pool.map(traverser._build_file_info, all_paths))
        file_infos = [file_info for file_info in maybe_infos if file_info is not None]
        repo_structure = traverser.get_repo_structure(file_infos)
    return traverser, file_infos, repo_structure


def _read_and_parse_misses(
    repo_path: Any,
    file_infos: list[Any],
    *,
    read_only: bool,
    timings: PhaseTimings | None,
    log: LogFn,
) -> tuple[Any, list[tuple], dict[int, Any], list[tuple[int, tuple, str]]]:
    """Read sources, split cache hits/misses, then parse only the misses."""
    from repowise.core.pipeline.phases.ingestion import _read_sources, _split_cached

    with timed(timings, "rebuild.read"):
        file_bytes = _read_sources(file_infos, None)
        parse_cache, cached_hits, to_parse = _split_cached(
            Path(repo_path), file_bytes, None, write_cache=not read_only
        )
    if read_only:
        parse_cache = None
    merged = _parse_misses(cached_hits, to_parse, parse_cache, timings, log)
    return parse_cache, file_bytes, merged, to_parse


def _new_update_graph_builder(
    repo_path: Any,
    exclude_patterns: list[str],
    *,
    read_only: bool,
    include_submodules: bool,
    include_nested_repos: bool,
    traverser: Any,
    file_infos: list[Any],
) -> Any:
    """Create the graph builder and attach traversal metadata used downstream."""
    from repowise.core.ingestion import GraphBuilder
    from repowise.core.workspace.update import get_head_commit

    builder = GraphBuilder(
        repo_path,
        exclude_patterns=exclude_patterns,
        centrality_cache_dir=None if read_only else Path(repo_path) / ".repowise",
        head_commit=get_head_commit(Path(repo_path)),
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
    )
    builder.console_scripts = traverser.console_script_names
    builder.traversal_stats = traverser.stats
    builder.traversed_file_paths = {file_info.path for file_info in file_infos}
    return builder


def _build_update_graph(
    repo_path: Any,
    graph_builder: Any,
    traverser: Any,
    file_bytes: list[tuple],
    merged: dict[int, Any],
    to_parse: list[tuple[int, tuple, str]],
    *,
    collect_sources: bool,
    include_submodules: bool,
    include_nested_repos: bool,
    parse_cache: Any,
    patch_requested: bool,
    seed_edges: Any | None,
    changed_paths: set[str] | None,
    timings: PhaseTimings | None,
    log: LogFn,
) -> tuple[list[Any], dict[str, bytes], int]:
    """Parse/build/resolve one update graph, preserving the patch/full timings."""
    if timings is not None:
        timings.start("rebuild.graph")
    try:
        parsed_files, source_map, unparsed = _add_parsed_files(
            graph_builder, file_bytes, merged, collect_sources
        )
        patch_plan, resolutions = _plan_graph_patch(
            repo_path,
            patch_requested=patch_requested,
            seed_edges=seed_edges,
            parse_cache=parse_cache,
            parsed_files=parsed_files,
            merged=merged,
            to_parse=to_parse,
            changed_paths=changed_paths,
        )
        graph_builder.patch_plan = patch_plan
        _wire_graph_builder(
            graph_builder,
            repo_path,
            include_submodules=include_submodules,
            include_nested_repos=include_nested_repos,
            source_map=source_map,
            pytest_roots=traverser.pytest_roots,
        )
        _build_graph_with_plan(
            graph_builder,
            patch_plan=patch_plan,
            resolutions=resolutions,
            parsed_files=parsed_files,
            seed_edges=seed_edges,
            timings=timings,
            log=log,
        )
        return parsed_files, source_map, unparsed
    finally:
        if timings is not None:
            timings.stop("rebuild.graph")


def build_repo_graph(
    repo_path: Any,
    exclude_patterns: list[str],
    *,
    collect_sources: bool = False,
    include_submodules: bool = False,
    include_nested_repos: bool = False,
    log: LogFn | None = None,
    timings: PhaseTimings | None = None,
    read_only: bool = False,
    seed_edges: Any | None = None,
    changed_paths: set[str] | None = None,
    patch_requested: bool = False,
    cache_resolutions: bool = False,
) -> tuple[list, dict[str, bytes], Any, Any, int]:
    """Traverse + parse the repo and build the graph (+ framework-aware edges).

    Shared by the incremental rebuild path (:func:`rebuild_graph_and_git`),
    the config-triggered re-score path and the commands that analyze the
    working tree live, so all of them build the same graph from the same
    parser and the same synthetic edge steps.

    ``read_only`` is for those live commands: the parse cache is read but
    never written, and no structural episodes or centrality cache are kept.

    Files that fail to read/parse are skipped and reported as a count rather than
    swallowed silently. ``source_map`` is populated only when ``collect_sources``
    is set; every path that persists the graph sets it, since the framework-edge
pass reads the sources.

    ``seed_edges`` is the last full graph snapshot. ``patch_requested`` says
    the experimental feature is enabled, including when a cache miss forces
    the safe full-build fallback. ``cache_resolutions`` opts into writing the
    supporting per-file import cache. ``changed_paths`` is the
    complete source/config diff from the update detector; it must exactly match
    the parsed cache misses before a patch is considered. This also catches
    changed manifests or excluded/unparsed files, which the parse cache alone
    cannot see. If the changed files' cross-file surface is untouched
    (:func:`~.surface.plan_patch`), only those files are re-resolved; otherwise
    the full build runs. The decision is left on ``graph_builder.patch_plan``.

    ``timings`` is the run's shared :class:`PhaseTimings` table; each step
    below records into it under a ``rebuild.*`` name. ``None`` records nothing.

    Returns ``(parsed_files, source_map, graph_builder, repo_structure,
    file_count)``.
    """
    log = log or _noop_log

    traverser, file_infos, repo_structure = _traverse_repo(
        repo_path,
        exclude_patterns,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
        timings=timings,
    )

    # Structural episodes, minus the formatter check: this path is the hot one
    # (every update, plus the config-triggered re-score), so it derives only
    # what the walk has already paid for and never spawns a subprocess.
    if not read_only:
        _record_structural_episodes(repo_path, traverser)

    # Thread-pool source reads + content-hash parse cache split, shared with
    # the init parse phase: only changed files need a tree-sitter parse.
    # Cache failures degrade to all-miss (full parse), as before.
    graph_builder = _new_update_graph_builder(
        repo_path,
        exclude_patterns,
        read_only=read_only,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
        traverser=traverser,
        file_infos=file_infos,
    )
    parse_cache, fi_and_bytes, merged, to_parse = _read_and_parse_misses(
        repo_path,
        file_infos,
        read_only=read_only,
        timings=timings,
        log=log,
    )

    skipped = len(file_infos) - len(fi_and_bytes)
    parsed_files, source_map, unparsed = _build_update_graph(
        repo_path,
        graph_builder,
        traverser,
        fi_and_bytes,
        merged,
        to_parse,
        collect_sources=collect_sources,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
        parse_cache=parse_cache,
        patch_requested=patch_requested,
        seed_edges=seed_edges,
        changed_paths=changed_paths,
        timings=timings,
        log=log,
    )
    skipped += unparsed
    if not read_only and cache_resolutions:
        from .resolution_cache import save_resolutions

        save_resolutions(Path(repo_path) / ".repowise", parsed_files)
    if parse_cache is not None:
        with timed(timings, "rebuild.cache_save"):
            parse_cache.save()

    if skipped:
        log(f"[yellow]Skipped {skipped} file(s) that failed to parse.[/yellow]")

    _add_synthetic_edges(repo_path, graph_builder, file_infos, source_map, log, timings)

    # The flag init sets after its graph build, so an update renders and keeps
    # a route-declaring file's api_contract page instead of demoting it.
    from repowise.core.generation import detect_code_api_contracts

    detect_code_api_contracts(parsed_files)

    return parsed_files, source_map, graph_builder, repo_structure, len(file_infos)


async def _index_git_metadata(
    repo_path: Any,
    file_diffs: list,
    cfg: dict,
    exclude_patterns: list[str],
    graph_builder: Any,
    source_map: dict[str, bytes],
    *,
    git_tier: str | None,
    force_full_git: bool,
    git_summary_sink: list[Any] | None,
    idle_decay_sink: dict[str, dict] | None,
    log: LogFn,
    timings: PhaseTimings | None,
) -> dict[str, dict]:
    """Reindex changed-file history and restore complete co-change graph edges."""
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.ingestion.git_indexer.tiers import GitIndexTier
    from repowise.core.pipeline.phases.git import label_co_change_structure

    git_meta_map: dict[str, dict] = {}
    if timings is not None:
        timings.start("rebuild.git")
    try:
        try:
            tier = GitIndexTier(git_tier) if git_tier else GitIndexTier.FULL
        except ValueError:
            tier = GitIndexTier.FULL
        git_indexer = GitIndexer(
            repo_path,
            commit_limit=cfg.get("commit_limit"),
            follow_renames=cfg.get("follow_renames", False),
            exclude_patterns=exclude_patterns or None,
            tier=tier,
        )
        if force_full_git:
            summary, updated_meta = await git_indexer.index_repo(
                repo_path.name, on_warning=log
            )
            if git_summary_sink is not None:
                git_summary_sink.append(summary)
            git_meta_map = {meta["file_path"]: meta for meta in updated_meta}
            label_co_change_structure(graph_builder, git_meta_map)
            graph_builder.update_co_change_edges(git_meta_map)
            graph_builder.co_change_edges_complete = True
        else:
            git_meta_map = await _index_changed_git_files(
                git_indexer,
                file_diffs,
                exclude_patterns,
                graph_builder,
                source_map,
                idle_decay_sink=idle_decay_sink,
                log=log,
                timings=timings,
            )
    except Exception as exc:
        log(f"[yellow]Git re-index skipped: {exc}[/yellow]")
        if force_full_git:
            raise
    finally:
        if timings is not None:
            timings.stop("rebuild.git")
    return git_meta_map


async def _index_changed_git_files(
    git_indexer: Any,
    file_diffs: list,
    exclude_patterns: list[str],
    graph_builder: Any,
    source_map: dict[str, bytes],
    *,
    idle_decay_sink: dict[str, dict] | None,
    log: LogFn,
    timings: PhaseTimings | None,
) -> dict[str, dict]:
    """Update changed-file git rows and rebuild co-change edges for the repo."""
    from repowise.core.pipeline.phases.git import label_co_change_structure

    changed_paths = build_filtered_changed_paths(file_diffs, exclude_patterns)
    co_change_full: dict[str, list[dict]] = {}
    updated_meta = await git_indexer.index_changed_files(
        changed_paths,
        all_files=set(source_map.keys()),
        co_change_sink=co_change_full,
        idle_decay_sink=idle_decay_sink,
        on_warning=log,
        timings=timings,
    )
    git_meta_map = {meta["file_path"]: meta for meta in updated_meta}
    label_co_change_structure(graph_builder, git_meta_map)
    if idle_decay_sink:
        # Idle rows carry the same column and are persisted on their own path.
        label_co_change_structure(graph_builder, idle_decay_sink)
    if co_change_full:
        graph_builder.update_co_change_edges(
            {
                path: {"co_change_partners_json": partners}
                for path, partners in co_change_full.items()
            }
        )
        graph_builder.co_change_edges_complete = True
    else:
        graph_builder.update_co_change_edges(git_meta_map)
    return git_meta_map


def _graph_update_decision(
    *, enabled: bool, seed_edges: Any | None, patch_plan: Any
) -> dict[str, Any]:
    """Persistable summary of why this update patched or rebuilt the graph."""
    if not enabled:
        return {
            "enabled": False,
            "mode": "full",
            "reason": "feature disabled",
            "files": [],
        }
    if seed_edges is None:
        return {
            "enabled": True,
            "mode": "full",
            "reason": "no valid persisted graph snapshot",
            "files": [],
        }
    if getattr(patch_plan, "eligible", False) is True:
        return {
            "enabled": True,
            "mode": "patch",
            "reason": "dependency surface unchanged",
            "files": list(patch_plan.scope),
        }
    return {
        "enabled": True,
        "mode": "full",
        "reason": getattr(patch_plan, "reason", None) or "patch plan unavailable",
        "files": [],
    }


async def _load_update_seed(
    repo_path: Any,
    *,
    enabled: bool,
    timings: PhaseTimings | None,
    log: LogFn,
) -> tuple[Any | None, Any | None]:
    """Load and invalidate the optional seed before any opted-in update write."""
    if not enabled:
        return None, None
    from .graph_seed import (
        invalidate_seed_edge_cache,
        load_seed_edges,
        load_seed_edges_for_reconciliation,
    )

    with timed(timings, "rebuild.seed_load"):
        seed_edges = await load_seed_edges(repo_path)
        previous_seed_edges = seed_edges or await load_seed_edges_for_reconciliation(repo_path)
    if seed_edges is None:
        log("[yellow]Full graph rebuild: no valid persisted edge snapshot[/yellow]")
    invalidate_seed_edge_cache(repo_path)
    return seed_edges, previous_seed_edges


async def _prime_graph_metrics(
    graph_builder: Any, timings: PhaseTimings | None, log: LogFn
) -> None:
    """Refresh global metrics without failing an otherwise valid update."""
    try:
        with timed(timings, "rebuild.metrics"):
            await graph_builder.compute_metrics_parallel()
    except Exception as exc:
        log(f"[yellow]Metric pre-computation skipped: {exc}[/yellow]")


async def rebuild_graph_and_git(
    repo_path: Any,
    file_diffs: list,
    cfg: dict,
    exclude_patterns: list[str],
    *,
    git_tier: str | None = None,
    include_submodules: bool = False,
    include_nested_repos: bool = False,
    idle_decay_sink: dict[str, dict] | None = None,
    force_full_git: bool = False,
    git_summary_sink: list[Any] | None = None,
    log: LogFn | None = None,
    timings: PhaseTimings | None = None,
    patch_graph: bool = False,
) -> tuple[list, dict[str, bytes], Any, Any, int, dict[str, dict]]:
    """Re-traverse + parse the repo, rebuild the graph (+ framework edges), and
    re-index git metadata for the changed files.

    ``patch_graph`` tries to reuse the persisted edges and re-resolve only the
    changed files; see :func:`build_repo_graph`. It falls back to the full
    rebuild whenever the change is not provably local.

    ``timings`` records the whole step as ``rebuild`` plus one ``rebuild.*``
    row per sub-step, so a slow update names the stage that made it slow.

    ``idle_decay_sink``, when provided, is filled with a decay-only partial
    metadata row for every idle (unchanged) file whose time-decayed history
    fields the anchor advance moved (issue #728). These rows are for the git
    persist step only — they are deliberately kept out of the returned
    ``git_meta_map`` so the partial health analysis (whose repo-wide aggregates
    read every entry) is unaffected.

    ``git_tier`` is the persisted ``state.json:git_tier`` value: a fast-mode
    (ESSENTIAL) repo must not pay per-file blame on every update for signals
    its index never had. Unknown/missing values fall back to FULL, matching
    the historical behavior for legacy state files.

    ``include_submodules`` / ``include_nested_repos`` are likewise read from
    state.json: a repo indexed with ``init --include-submodules`` must not
    silently drop its submodule files on every incremental update. Missing
    keys fall back to False (legacy behavior).

    ``force_full_git`` is reserved for changes to Git-history/traversal
    configuration.  Those settings invalidate every per-file row, not merely
    files in the commit diff.  The optional sink carries the resulting summary
    to persistence so per-commit/fix-event rows can be replaced from the same
    walk.

    Returns ``(parsed_files, source_map, graph_builder, repo_structure,
    file_count, git_meta_map)``.
    """
    log = log or _noop_log
    if timings is not None:
        timings.start("rebuild")

    seed_edges, previous_seed_edges = await _load_update_seed(
        repo_path, enabled=patch_graph, timings=timings, log=log
    )

    # Full re-ingest for graph (needed for cascade analysis)
    parsed_files, source_map, graph_builder, repo_structure, file_count = build_repo_graph(
        repo_path,
        exclude_patterns,
        collect_sources=True,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
        log=log,
        timings=timings,
        seed_edges=seed_edges,
        patch_requested=patch_graph,
        cache_resolutions=patch_graph,
        changed_paths={
            path
            for fd in file_diffs
            for path in (getattr(fd, "path", None), getattr(fd, "old_path", None))
            if path
        },
    )
    if previous_seed_edges is not None:
        graph_builder.previous_seed_edges = previous_seed_edges
        if not getattr(getattr(graph_builder, "patch_plan", None), "eligible", False):
            from .graph_seed import restore_unparsed_graph_rows

            with timed(timings, "rebuild.seed_reconcile"):
                restore_unparsed_graph_rows(
                    graph_builder,
                    previous_seed_edges,
                    {pf.file_info.path for pf in parsed_files},
                )

    git_meta_map = await _index_git_metadata(
        repo_path,
        file_diffs,
        cfg,
        exclude_patterns,
        graph_builder,
        source_map,
        git_tier=git_tier,
        force_full_git=force_full_git,
        git_summary_sink=git_summary_sink,
        idle_decay_sink=idle_decay_sink,
        log=log,
        timings=timings,
    )

    # Global metrics must reflect the patched graph; the persistence upsert gate
    # avoids rewriting unchanged rows, but the metric values must still be fresh.
    await _prime_graph_metrics(graph_builder, timings, log)

    graph_builder.incremental_graph_decision = _graph_update_decision(
        enabled=patch_graph,
        seed_edges=seed_edges,
        patch_plan=getattr(graph_builder, "patch_plan", None),
    )

    if timings is not None:
        timings.stop("rebuild")
    return parsed_files, source_map, graph_builder, repo_structure, file_count, git_meta_map
