"""Rebuild the repo graph and re-index git metadata for the changed files."""

from __future__ import annotations

import gc
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


def _report_unavailable_grammars(to_parse: list, log: LogFn) -> None:
    """Name each runtime grammar the misses need that will not load, once, before the pool.

    Loading here also fills the grammar cache before the parse workers start.
    Best-effort: a reporting failure must not stop an update.
    """
    from collections import Counter

    try:
        from repowise.core.ingestion.parser import unavailable_runtime_grammars
        from repowise.core.ingestion.parser.grammars import runtime_grammar_warning

        counts = Counter(item[0].language for _idx, item, _hash in to_parse)
        for tag, reason in unavailable_runtime_grammars(counts):
            log(f"[yellow]{runtime_grammar_warning(tag, reason, counts[tag])}[/yellow]")
    except Exception:
        logger.debug("update_grammar_preflight_failed", exc_info=True)


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
    _report_unavailable_grammars(to_parse, log)
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


def _manifest_sources(file_bytes: list, merged: dict[int, Any]) -> dict[str, bytes]:
    """The bytes of every parsed JSON file: the manifests that map imports to paths."""
    return {
        fi.path: source
        for idx, (fi, source) in enumerate(file_bytes)
        if (parsed := merged.get(idx)) is not None and parsed.file_info.language == "json"
    }


def _add_synthetic_edges(
    repo_path: Any,
    graph_builder: Any,
    file_infos: list,
    source_map: dict[str, bytes],
    log: LogFn,
    timings: PhaseTimings | None,
) -> None:
    """Add the framework-aware and dynamic-hint edges to a built graph.

    Sets ``graph_builder.synthetic_edges_complete`` to whether both passes ran,
    so the edge write knows the graph is missing their edges this time.
    """
    graph_builder.synthetic_edges_complete = True
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
        graph_builder.synthetic_edges_complete = False
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
                cache_dir=getattr(graph_builder, "_cache_dir", None),
            )
            graph_builder.add_dynamic_edges(dynamic_edges)
        if dynamic_edges:
            log(f"Dynamic hint edges added: [cyan]{len(dynamic_edges)}[/cyan]")

    except Exception as exc:
        graph_builder.synthetic_edges_complete = False
        logger.warning("dynamic_hints_extraction_failed", error=str(exc))
        log(
            f"[yellow]Dynamic hint extraction failed: {exc}. Dynamic edges"
            "will be missing from this update.[/yellow]"
        )


def _plan_graph_patch(
    repo_path: Any,
    graph_builder: Any,
    *,
    patch_requested: bool,
    seed_edges: Any | None,
    parse_cache: Any,
    parsed_files: list[Any],
    merged: dict[int, Any],
    to_parse: list[tuple[int, tuple, str]],
    changed_paths: set[str] | None,
    traversed_paths: set[str],
    manifest_sources: dict[str, bytes],
    presence_changed: set[str] | None = None,
    current_inputs: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, tuple] | None]:
    """Return the safe patch plan and restored-import cache, if eligible."""
    from .resolution_cache import load_cache
    from .surface import PatchPlan, plan_patch

    if not patch_requested:
        return PatchPlan(False, reason="feature disabled"), None
    if seed_edges is None:
        return PatchPlan(False, reason="no valid persisted graph snapshot"), None
    if parse_cache is None:
        return PatchPlan(False, reason="no parse cache"), None

    changed = _fresh_changes(parse_cache, merged, to_parse)
    unparsed = _unparsed_changes(parse_cache, parsed_files, changed, changed_paths, traversed_paths)
    if unparsed is None:
        return (
            PatchPlan(
                False,
                reason="detected changes include unparsed files or do not match the parse cache",
            ),
            None,
        )
    resolutions, record = load_cache(Path(repo_path) / ".repowise")
    refusal, cleared = _checked_non_source(
        repo_path,
        graph_builder,
        unparsed,
        changed,
        parse_cache,
        presence_changed,
        current_inputs=current_inputs,
        record=record,
    )
    if refusal is not None:
        return PatchPlan(False, reason=refusal), None

    patch_plan = plan_patch(
        parse_cache=parse_cache,
        parsed_files=parsed_files,
        changed=changed,
        traversed_paths=traversed_paths,
        seed_nodes=seed_edges.get("nodes", ()),
        cleared=cleared,
        manifest_sources=manifest_sources,
    )
    if not patch_plan.eligible:
        return patch_plan, None

    patch_plan, kept = _plan_with_resolutions(
        graph_builder,
        patch_plan,
        resolutions,
        parsed_files=parsed_files,
        seed_edges=seed_edges,
    )
    return _refreshed_for_non_source(patch_plan, bool(unparsed or cleared)), kept


def _fresh_changes(
    parse_cache: Any, merged: dict[int, Any], to_parse: list[tuple[int, tuple, str]]
) -> dict[str, Any]:
    """The parse-cache misses whose parse differs from the last run's, by path.

    A file whose grammar is unavailable misses on every run so that it parses
    properly once the grammar loads. While it still has none and its bytes
    are those the last run saw, its parse is the last run's: not a change.
    """
    changed: dict[str, Any] = {}
    for idx, (fi, _source), content_hash in to_parse:
        parsed = merged.get(idx)
        if parsed is None:
            continue
        if parsed.grammar_missing and parse_cache.was_unparsed(fi.path, content_hash):
            continue
        changed[fi.path] = parsed
    return changed


def _unparsed_changes(
    parse_cache: Any,
    parsed_files: list[Any],
    changed: dict[str, Any],
    changed_paths: set[str] | None,
    traversed_paths: set[str],
) -> set[str] | None:
    """Detected changes the walk did not index now or before; ``None`` on a mismatch.

    A mismatch is a parse miss the diff did not report, or a detected change
    to an indexed file that has no fresh parse.
    """
    known = parse_cache.known_paths()
    detected = set(changed) if changed_paths is None else set(changed_paths)
    # A deleted file has no parse; plan_patch decides whether its removal is safe.
    detected -= known - {pf.file_info.path for pf in parsed_files}
    unparsed = detected - set(changed)
    if set(changed) - detected or unparsed & (traversed_paths | known):
        return None
    return unparsed


def _checked_non_source(*args: Any, **kwargs: Any) -> tuple[str | None, frozenset[str]]:
    """:func:`_plan_non_source`, where a checker bug costs a full build, not the update."""
    try:
        return _plan_non_source(*args, **kwargs)
    except Exception as exc:
        logger.warning("graph_patch_config_check_failed", error=str(exc))
        return f"config check failed: {exc}", frozenset()


def _refreshed_for_non_source(patch_plan: Any, non_source: bool) -> Any:
    """*patch_plan*, refreshing when the update cleared config, data or unindexed files.

    The framework and dynamic-hint edges are then re-derived over the whole
    graph rather than trusted from the seed: a config reader the gate does
    not know of costs a slower patch, not a stale edge.
    """
    from dataclasses import replace

    if not (patch_plan.eligible and non_source) or patch_plan.refresh:
        return patch_plan
    return replace(patch_plan, refresh=True)


def _plan_non_source(
    repo_path: Any,
    graph_builder: Any,
    unparsed: set[str],
    changed: dict[str, Any],
    parse_cache: Any,
    presence_changed: set[str] | None,
    *,
    current_inputs: dict[str, Any] | None,
    record: dict[str, Any] | None,
) -> tuple[str | None, frozenset[str]]:
    """Check the update's config, data and unindexed files (:mod:`.config_inputs`)."""
    from .config_inputs import plan_non_source_changes

    root = Path(repo_path)
    resolver = getattr(graph_builder, "_tsconfig_resolver", None)
    config_reads = set()
    for path in getattr(resolver, "read_paths", ()):
        try:
            config_reads.add(Path(path).resolve().relative_to(root.resolve()).as_posix())
        except ValueError:
            continue
    return plan_non_source_changes(
        unparsed=unparsed,
        changed=changed,
        previous_parse=lambda path, parsed: parse_cache.previous_entry(
            path, exclude_hash=parsed.content_hash
        ),
        presence_changed=presence_changed,
        config_reads=config_reads,
        current=current_inputs,
        record=record,
    )


def _with_field_changes(graph_builder: Any, patch_plan: Any, resolutions: dict) -> Any:
    """*patch_plan*, made refreshing when an edit changed the field types a class declares.

    Another file's ``obj.field.method()`` is typed from those declarations,
    which no parse surface records: the files that spell a changed field must
    be re-resolved too.
    """
    from dataclasses import replace

    from .affected import changed_field_names
    from .surface import PatchPlan

    parsed_files = graph_builder._parsed_files
    paths = (*patch_plan.scope, *patch_plan.removed)
    c_paths = [p for p in paths if p in parsed_files and parsed_files[p].file_info.language in ("c", "cpp")]
    names = changed_field_names(parsed_files, graph_builder._source_map, c_paths, resolutions)
    others = changed_field_names(
        parsed_files, graph_builder._source_map, [p for p in paths if p not in c_paths], resolutions
    )
    if names is None or others is None:
        return PatchPlan(False, reason="an edited file has no field record to compare")
    if not names and not others:
        return patch_plan
    # A C/C++ file's fields and facts are read through source text only by
    # C/C++ files (see ``PatchPlan.include_only``).
    include_only = (patch_plan.include_only | names) - others - (patch_plan.names - patch_plan.include_only)
    return replace(
        patch_plan, refresh=True, names=patch_plan.names | names | others, include_only=include_only
    )


def _refined_plan(
    graph_builder: Any, patch_plan: Any, seed_edges: Any, resolutions: dict[str, tuple]
) -> Any:
    """*patch_plan* checked for retyped fields and, when refreshing, widened to its reach.

    A failure here is a planner bug: it costs a full build, not the update.
    """
    from .surface import PatchPlan

    try:
        patch_plan = _with_field_changes(graph_builder, patch_plan, resolutions)
        if not patch_plan.refresh:
            return patch_plan
        from .affected import expand_scope

        return expand_scope(graph_builder, patch_plan, seed=seed_edges, resolutions=resolutions)
    except Exception as exc:
        logger.warning("graph_patch_scope_failed", error=str(exc))
        return PatchPlan(False, reason=f"patch planning failed: {exc}")


def _plan_with_resolutions(
    graph_builder: Any,
    patch_plan: Any,
    resolutions: dict[str, tuple] | None,
    *,
    parsed_files: list[Any],
    seed_edges: Any,
) -> tuple[Any, dict[str, tuple] | None]:
    """Accept an eligible plan only when saved resolutions cover every unchanged file.

    A refreshing plan is then widened to every file its edit reaches.
    """
    from .resolution_cache import covers
    from .surface import PatchPlan
    if covers(resolutions, parsed_files, patch_plan.scope):
        patch_plan = _refined_plan(graph_builder, patch_plan, seed_edges, resolutions)
        return patch_plan, (resolutions if patch_plan.eligible else None)
    if resolutions is None:
        return PatchPlan(False, reason="no import resolution cache from this parser version"), None

    missing_path = next(
        (
            pf.file_info.path
            for pf in parsed_files
            if pf.file_info.path not in patch_plan.scope
            and (
                pf.file_info.path not in resolutions
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
            restore_resolutions(
                resolutions, parsed_files, patch_plan.scope, graph_builder.import_resolutions
            )
            seeded = graph_builder.seed_graph_snapshot(
                seed_edges, drop_paths=patch_plan.removed, scope=patch_plan.scope
            )
            log(
                f"Patching the graph: re-resolving {len(patch_plan.scope)} file(s), "
                f"reusing {seeded:,} edges"
            )
            graph_builder.build(scope=patch_plan.scope, refresh_global=patch_plan.refresh)
        return

    with timed(timings, "rebuild.graph.full"):
        if seed_edges is not None:
            log(f"[yellow]Full graph rebuild: {patch_plan.reason}[/yellow]")
        graph_builder.build()


def _include_reach_refusal(graph_builder: Any, seed_edges: Any) -> str | None:
    """Why a patched graph must be rebuilt in full (:mod:`.include_reach`), or ``None``.

    A checker failure is a refusal: it costs a full build, not a wrong graph.
    """
    from .include_reach import include_reach_refusal

    try:
        return include_reach_refusal(graph_builder._graph, seed_edges, graph_builder._parsed_files)
    except Exception as exc:
        logger.warning("graph_patch_reach_check_failed", error=str(exc))
        return f"include reach check failed: {exc}"


def _traverse_repo(
    repo_path: Any,
    exclude_patterns: list[str],
    *,
    include_submodules: bool,
    include_nested_repos: bool,
    timings: PhaseTimings | None,
) -> tuple[Any, list[Any], Any, list[str]]:
    """Walk once, build file metadata in parallel, and derive repo structure.

    Also returns every walked path, indexed or not, relative to the repo.
    """
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
    root = traverser.repo_root
    walked = [path.relative_to(root).as_posix() for path in all_paths]
    return traverser, file_infos, repo_structure, walked


def _read_and_parse_misses(
    repo_path: Any,
    file_infos: list[Any],
    stats: Any,
    *,
    read_only: bool,
    timings: PhaseTimings | None,
    log: LogFn,
) -> tuple[Any, list[tuple], dict[int, Any], list[tuple[int, tuple, str]]]:
    """Read sources, split cache hits/misses, then parse only the misses.

    A document whose Markdown is over the ceiling leaves *file_infos* in place,
    so it drops out of the index scope like a file the walk skipped.
    """
    from repowise.core.pipeline.phases.ingestion import (
        _read_sources,
        _skipped_source_line,
        _split_cached,
        drop_skipped_documents,
        write_skipped_files_report,
    )

    with timed(timings, "rebuild.read"):
        file_bytes = _read_sources(file_infos, None)
        parse_cache, cached_hits, to_parse, skipped = _split_cached(
            Path(repo_path), file_bytes, None, write_cache=not read_only
        )
    drop_skipped_documents(file_infos, stats, skipped)
    for record in skipped:
        log(f"[yellow]{_skipped_source_line(record).strip()}[/yellow]")
    if read_only:
        parse_cache = None
    else:
        write_skipped_files_report(Path(repo_path), stats)
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
    presence_changed: set[str] | None,
    current_inputs: dict[str, Any] | None,
    timings: PhaseTimings | None,
    log: LogFn,
    refusal: str | None = None,
) -> tuple[list[Any], dict[str, bytes], int, str | None]:
    """Parse/build/resolve one update graph, preserving the patch/full timings.

    *refusal* skips the patch planning and builds in full for that reason.
    The last value returned is why a patched graph must be rebuilt in full
    after all (:mod:`.include_reach`), or ``None``.
    """
    if timings is not None:
        timings.start("rebuild.graph")
    # The cyclic collector is off for the build. What it allocates (nodes,
    # edges, resolver indexes) lives past it, and every full collection its
    # allocations trigger walks the seed snapshot and every parse again: 13-15 s
    # of a 60-75 s patch on a 62k-file repository, with no cycle to free.
    # Ceiling: cyclic garbage made during the build waits for the next
    # collection after it; reference counting still frees everything else.
    collecting = gc.isenabled()
    gc.disable()
    try:
        parsed_files, source_map, unparsed = _add_parsed_files(
            graph_builder, file_bytes, merged, collect_sources
        )
        # Wired before planning: a patch that adds or removes files re-resolves
        # unchanged files' imports with the same resolvers the build uses.
        _wire_graph_builder(
            graph_builder,
            repo_path,
            include_submodules=include_submodules,
            include_nested_repos=include_nested_repos,
            source_map=source_map,
            pytest_roots=traverser.pytest_roots,
        )
        if refusal is None:
            patch_plan, resolutions = _plan_graph_patch(
                repo_path,
                graph_builder,
                patch_requested=patch_requested,
                seed_edges=seed_edges,
                parse_cache=parse_cache,
                parsed_files=parsed_files,
                merged=merged,
                to_parse=to_parse,
                changed_paths=changed_paths,
                traversed_paths=graph_builder.traversed_file_paths,
                presence_changed=presence_changed,
                current_inputs=current_inputs,
                manifest_sources=_manifest_sources(file_bytes, merged),
            )
        else:
            from .surface import PatchPlan

            patch_plan, resolutions = PatchPlan(False, reason=refusal), None
        graph_builder.patch_plan = patch_plan
        _build_graph_with_plan(
            graph_builder,
            patch_plan=patch_plan,
            resolutions=resolutions,
            parsed_files=parsed_files,
            seed_edges=seed_edges,
            timings=timings,
            log=log,
        )
        reach_refusal = (
            _include_reach_refusal(graph_builder, seed_edges) if patch_plan.eligible else None
        )
        return parsed_files, source_map, unparsed, reach_refusal
    finally:
        if collecting:
            gc.enable()
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
    presence_changed: set[str] | None = None,
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
    the incremental graph feature is enabled, including when a cache miss forces
    the safe full-build fallback. ``cache_resolutions`` opts into writing the
    supporting per-file import cache. ``changed_paths`` is the
    complete source/config diff from the update detector; deleted files aside,
    it must exactly match the parsed cache misses before a patch is considered.
    This also catches changed manifests or excluded/unparsed files, which the
    parse cache alone cannot see; ``presence_changed`` names its added, deleted
    and renamed paths (``None``: unknown), which an unindexed file must not be
    among for its edit to patch (:mod:`.config_inputs`). If the changed files'
    cross-file surface is untouched
    (:func:`~.surface.plan_patch`), only those files are re-resolved; otherwise
    the full build runs. The decision is left on ``graph_builder.patch_plan``.

    ``timings`` is the run's shared :class:`PhaseTimings` table; each step
    below records into it under a ``rebuild.*`` name. ``None`` records nothing.

    Returns ``(parsed_files, source_map, graph_builder, repo_structure,
    file_count)``.
    """
    log = log or _noop_log

    traverser, file_infos, repo_structure, walked = _traverse_repo(
        repo_path,
        exclude_patterns,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
        timings=timings,
    )

    # What the build is about to read from its manifests, taken before it reads
    # them: an edit racing the build then leaves the record older, not newer.
    current_inputs = None
    if not read_only and (patch_requested or cache_resolutions):
        from .config_inputs import record_inputs

        current_inputs = record_inputs(Path(repo_path), walked)

    # Structural episodes, minus the formatter check: this path is the hot one
    # (every update, plus the config-triggered re-score), so it derives only
    # what the walk has already paid for and never spawns a subprocess.
    if not read_only:
        _record_structural_episodes(repo_path, traverser)

    # Thread-pool source reads + content-hash parse cache split, shared with
    # the init parse phase: only changed files need a tree-sitter parse.
    # Cache failures degrade to all-miss (full parse), as before.
    parse_cache, fi_and_bytes, merged, to_parse = _read_and_parse_misses(
        repo_path,
        file_infos,
        traverser.stats,
        read_only=read_only,
        timings=timings,
        log=log,
    )
    refusal = None
    while True:
        # Built after the read, which can still drop a document from file_infos.
        graph_builder = _new_update_graph_builder(
            repo_path,
            exclude_patterns,
            read_only=read_only,
            include_submodules=include_submodules,
            include_nested_repos=include_nested_repos,
            traverser=traverser,
            file_infos=file_infos,
        )
        parsed_files, source_map, unparsed, refusal = _build_update_graph(
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
            presence_changed=presence_changed,
            current_inputs=current_inputs,
            timings=timings,
            log=log,
            refusal=refusal,
        )
        # A patch whose include reach moved is redone as a full build, once.
        if refusal is None:
            break
    skipped = len(file_infos) - len(fi_and_bytes) + unparsed
    if not read_only and cache_resolutions:
        from .resolution_cache import save_resolutions

        save_resolutions(
            Path(repo_path) / ".repowise",
            parsed_files,
            graph_builder.import_resolutions,
            source_map,
            config_inputs=current_inputs,
        )
    if parse_cache is not None:
        with timed(timings, "rebuild.cache_save"):
            parse_cache.save()

    if skipped:
        log(f"[yellow]Skipped {skipped} file(s) that failed to parse.[/yellow]")

    _add_synthetic_edges(repo_path, graph_builder, file_infos, source_map, log, timings)
    # A refreshing patch set aside re-resolved files' co-change edges; a full
    # build adds them after every other edge, so they go back only now.
    graph_builder.restore_co_change_edges()

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
            "reason": (
                "re-resolved every file the edit reaches"
                if getattr(patch_plan, "refresh", False)
                else "dependency surface unchanged"
            ),
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
    """Load and invalidate the optional seed before any update write that uses it."""
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
    fresh_communities: bool = False,
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
        # Only a plain in-place edit proves the file existed before and after.
        presence_changed={
            path
            for fd in file_diffs
            if getattr(fd, "status", None) != "modified"
            or getattr(fd, "old_path", None) not in (None, getattr(fd, "path", None))
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
    # A full re-score detects communities afresh rather than reusing a drifted partition.
    graph_builder.fresh_communities = fresh_communities
    await _prime_graph_metrics(graph_builder, timings, log)

    graph_builder.incremental_graph_decision = _graph_update_decision(
        enabled=patch_graph,
        seed_edges=seed_edges,
        patch_plan=getattr(graph_builder, "patch_plan", None),
    )

    if timings is not None:
        timings.stop("rebuild")
    return parsed_files, source_map, graph_builder, repo_structure, file_count, git_meta_map
