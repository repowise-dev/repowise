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

    ``timings`` is the run's shared :class:`PhaseTimings` table; each step
    below records into it under a ``rebuild.*`` name. ``None`` records nothing.

    Returns ``(parsed_files, source_map, graph_builder, repo_structure,
    file_count)``.
    """
    import os
    from concurrent.futures import ThreadPoolExecutor

    from repowise.core.ingestion import FileTraverser, GraphBuilder

    log = log or _noop_log

    traverser = FileTraverser(
        repo_path,
        extra_exclude_patterns=exclude_patterns or None,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
    )
    # Parallel stat + header sniffing, mirroring the init ingestion phase.
    # A serial traverse() pays per-file I/O latency sequentially; on a cold
    # OS file cache that was ~50s of every PowerToys-scale update. Passing
    # the file_infos into get_repo_structure also avoids its re-walk.
    with timed(timings, "rebuild.traverse"):
        all_paths = list(traverser._walk())
        io_workers = min(32, max(4, (os.cpu_count() or 4) * 2))
        with ThreadPoolExecutor(max_workers=io_workers) as io_pool:
            maybe_infos = list(io_pool.map(traverser._build_file_info, all_paths))
        file_infos = [fi for fi in maybe_infos if fi is not None]
        repo_structure = traverser.get_repo_structure(file_infos)

    # Structural episodes, minus the formatter check: this path is the hot one
    # (every update, plus the config-triggered re-score), so it derives only
    # what the walk has already paid for and never spawns a subprocess.
    if not read_only:
        _record_structural_episodes(repo_path, traverser)

    # Thread-pool source reads + content-hash parse cache split, shared with
    # the init parse phase: only changed files need a tree-sitter parse.
    # Cache failures degrade to all-miss (full parse), as before.
    from repowise.core.pipeline.phases.ingestion import (
        _read_sources,
        _split_cached,
    )
    from repowise.core.workspace.update import get_head_commit

    with timed(timings, "rebuild.read"):
        fi_and_bytes = _read_sources(file_infos, None)
        parse_cache, cached_hits, to_parse = _split_cached(Path(repo_path), fi_and_bytes, None)
    if read_only:
        parse_cache = None  # hits above still count; misses are not stored

    graph_builder = GraphBuilder(
        repo_path,
        exclude_patterns=exclude_patterns,
        centrality_cache_dir=None if read_only else Path(repo_path) / ".repowise",
        head_commit=get_head_commit(Path(repo_path)),
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
    )
    graph_builder.console_scripts = traverser.console_script_names
    # Carry the walk's skip record to the dead-code analyzer. This report is
    # persisted repo-wide, so an update that could not see the skipped source
    # files would re-derive and write back the verdicts init had clamped.
    graph_builder.traversal_stats = traverser.stats
    # Reconciliation authority is the successful traversal, not the successful
    # parse set. A transient read/parser failure must not make an existing
    # in-scope file look deleted and prune its persisted rows.
    graph_builder.traversed_file_paths = {
        file_info.path for file_info in file_infos
    }

    # Misses go through init's deadline pool. An update pays one pool start
    # (about a second on Windows) when anything changed, in exchange for a
    # file that never finishes parsing costing one deadline instead of the run.
    merged = _parse_misses(cached_hits, to_parse, parse_cache, timings, log)

    skipped = len(file_infos) - len(fi_and_bytes)  # unreadable files
    # Opened here and closed after build(): graph construction is the add_file
    # loop plus the resolver, and the tsconfig wiring sits between them.
    if timings is not None:
        timings.start("rebuild.graph")
    parsed_files, source_map, unparsed = _add_parsed_files(
        graph_builder, fi_and_bytes, merged, collect_sources
    )
    skipped += unparsed

    # TS/JS path aliases (``@/components/...``) resolve only when the
    # tsconfig resolver is attached before build(); without it the alias
    # targets read as external nodes and every aliased file looks
    # unreachable to the dead-code analyzer (#648 — this rebuild path was
    # missed when the CLI commands were wired).
    from repowise.core.ingestion import wire_tsconfig_resolver

    wire_tsconfig_resolver(
        graph_builder,
        repo_path,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
    )
    graph_builder.set_source_map(source_map)
    graph_builder.set_pytest_roots(traverser.pytest_roots)
    graph_builder.build()
    if timings is not None:
        timings.stop("rebuild.graph")
    if parse_cache is not None:
        with timed(timings, "rebuild.cache_save"):
            parse_cache.save()

    if skipped:
        log(f"[yellow]Skipped {skipped} file(s) that failed to parse.[/yellow]")

    _add_synthetic_edges(repo_path, graph_builder, file_infos, source_map, log, timings)

    return parsed_files, source_map, graph_builder, repo_structure, len(file_infos)


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
) -> tuple[list, dict[str, bytes], Any, Any, int, dict[str, dict]]:
    """Re-traverse + parse the repo, rebuild the graph (+ framework edges), and
    re-index git metadata for the changed files.

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

    # Full re-ingest for graph (needed for cascade analysis)
    parsed_files, source_map, graph_builder, repo_structure, file_count = build_repo_graph(
        repo_path,
        exclude_patterns,
        collect_sources=True,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
        log=log,
        timings=timings,
    )

    # Re-index git metadata for changed files
    git_meta_map: dict[str, dict] = {}
    if timings is not None:
        timings.start("rebuild.git")
    try:
        from repowise.core.ingestion.git_indexer import GitIndexer
        from repowise.core.ingestion.git_indexer.tiers import GitIndexTier
        from repowise.core.pipeline.phases.git import label_co_change_structure

        try:
            tier = GitIndexTier(git_tier) if git_tier else GitIndexTier.FULL
        except ValueError:
            tier = GitIndexTier.FULL
        _commit_limit = cfg.get("commit_limit")
        _follow_renames = cfg.get("follow_renames", False)
        git_indexer = GitIndexer(
            repo_path,
            commit_limit=_commit_limit,
            follow_renames=_follow_renames,
            exclude_patterns=exclude_patterns or None,
            tier=tier,
        )
        if force_full_git:
            summary, updated_meta = await git_indexer.index_repo(repo_path.name, on_warning=log)
            if git_summary_sink is not None:
                git_summary_sink.append(summary)
            git_meta_map = {m["file_path"]: m for m in updated_meta}
            label_co_change_structure(graph_builder, git_meta_map)
            graph_builder.update_co_change_edges(git_meta_map)
        else:
            changed_paths = build_filtered_changed_paths(file_diffs, exclude_patterns)
            # The full tracked-file set lets the indexer re-run the repo-wide
            # co-change walk so partners aren't wiped to "[]" for changed files.
            # The sink captures that walk's FULL per-file partner map: the graph
            # was just rebuilt from scratch, so co_changes edges must be re-added
            # for every file (not only the changed ones) or the update graph
            # diverges from the init graph and the centrality cache can't hit.
            co_change_full: dict[str, list[dict]] = {}
            updated_meta = await git_indexer.index_changed_files(
                changed_paths,
                all_files=set(source_map.keys()),
                co_change_sink=co_change_full,
                idle_decay_sink=idle_decay_sink,
                on_warning=log,
                timings=timings,
            )
            git_meta_map = {m["file_path"]: m for m in updated_meta}
            label_co_change_structure(graph_builder, git_meta_map)
            if idle_decay_sink:
                # Idle rows carry the same column and are persisted on their own
                # path, having been serialized before the graph existed.
                label_co_change_structure(graph_builder, idle_decay_sink)
            if co_change_full:
                graph_builder.update_co_change_edges(
                    {
                        fp: {"co_change_partners_json": partners}
                        for fp, partners in co_change_full.items()
                    }
                )
            else:
                graph_builder.update_co_change_edges(git_meta_map)
    except Exception as exc:
        log(f"[yellow]Git re-index skipped: {exc}[/yellow]")
        if force_full_git:
            raise
    finally:
        if timings is not None:
            timings.stop("rebuild.git")

    # Pre-compute centrality/community metrics with the init path's fan-out
    # parallelism. Without this, persist_graph_nodes computes the same
    # metrics lazily one-by-one. Runs after the co-change edge refresh so
    # the cached subgraphs reflect the final structure. Best-effort: every
    # metric still falls back to lazy computation.
    try:
        with timed(timings, "rebuild.metrics"):
            await graph_builder.compute_metrics_parallel()
    except Exception as exc:
        log(f"[yellow]Metric pre-computation skipped: {exc}[/yellow]")

    if timings is not None:
        timings.stop("rebuild")
    return parsed_files, source_map, graph_builder, repo_structure, file_count, git_meta_map
