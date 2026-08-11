"""Incremental (changed-files) index refresh.

The orchestration that `repowise update` runs for an already-indexed repo:
re-ingest the graph (parse-cache backed), re-index git metadata for the
changed files only, run partial health/dead-code analysis, and upsert the
results — without the full pipeline's delete-then-insert persistence or LLM
generation.

Extracted from the CLI update command so workspace updates can route
already-indexed member repos through the same incremental path instead of
re-running the full init pipeline per repo. The CLI keeps thin wrappers
that delegate here.

Progress/diagnostic messages go through an optional ``log`` callback (the
CLI passes ``console.print``; messages use rich markup). When ``log`` is
omitted the messages are dropped — every one of them annotates a
best-effort step that already degrades gracefully.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

LogFn = Callable[[str], None]


def _noop_log(message: str) -> None:  # pragma: no cover - trivial
    return None


def build_filtered_changed_paths(file_diffs: list, exclude_patterns: list[str]) -> list[str]:
    """Extract paths from file_diffs, filtering out excluded patterns."""
    paths = [fd.path for fd in file_diffs]
    if not exclude_patterns:
        return paths
    import pathspec

    spec = pathspec.PathSpec.from_lines("gitwildmatch", exclude_patterns)
    return [p for p in paths if not spec.match_file(p)]


def build_repo_graph(
    repo_path: Any,
    exclude_patterns: list[str],
    *,
    collect_sources: bool = False,
    include_submodules: bool = False,
    include_nested_repos: bool = False,
    log: LogFn | None = None,
) -> tuple[list, dict[str, bytes], Any, Any, int]:
    """Traverse + parse the repo and build the graph (+ framework-aware edges).

    Shared by the incremental rebuild path (:func:`rebuild_graph_and_git`) and
    the config-triggered re-score path so both build the same graph from the
    same parser and the same synthetic edge step.

    Files that fail to read/parse are skipped and reported as a count rather than
    swallowed silently. ``source_map`` is populated only when ``collect_sources``
    is set (the re-score path doesn't need the raw bytes).

    Returns ``(parsed_files, source_map, graph_builder, repo_structure,
    file_count)``.
    """
    import os
    from concurrent.futures import ThreadPoolExecutor

    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

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
    all_paths = list(traverser._walk())
    io_workers = min(32, max(4, (os.cpu_count() or 4) * 2))
    with ThreadPoolExecutor(max_workers=io_workers) as io_pool:
        maybe_infos = list(io_pool.map(traverser._build_file_info, all_paths))
    file_infos = [fi for fi in maybe_infos if fi is not None]
    repo_structure = traverser.get_repo_structure(file_infos)

    # Structural episodes, minus the formatter check: this path is the hot one
    # (every update, plus the config-triggered re-score), so it derives only
    # what the walk has already paid for and never spawns a subprocess.
    try:
        from repowise.core.precedent.structural import record_structural_episodes

        record_structural_episodes(repo_path, traverser, allow_formatter_check=False)
    except Exception:
        pass

    # Thread-pool source reads + content-hash parse cache split, shared with
    # the init parse phase: only changed files need a tree-sitter parse.
    # Cache failures degrade to all-miss (full parse), as before.
    from repowise.core.pipeline.phases.ingestion import (
        _cache_parsed,
        _read_sources,
        _split_cached,
    )

    fi_and_bytes = _read_sources(file_infos, None)
    parse_cache, cached_hits, to_parse = _split_cached(Path(repo_path), fi_and_bytes, None)

    parser: Any = None  # constructed lazily — every-file-cached updates skip query compilation
    parsed_files: list = []
    source_map: dict[str, bytes] = {}
    graph_builder = GraphBuilder(
        repo_path,
        exclude_patterns=exclude_patterns,
        centrality_cache_dir=Path(repo_path) / ".repowise",
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
    )

    # Parse the misses in process and serially: on the update path they are
    # change-sized. (Ceiling: a wiped/stale cache re-parses everything on one
    # core; routing large miss counts through init's process pool would lift
    # it, at the cost of Windows spawn overhead on every routine update.)
    merged: dict[int, Any] = dict(cached_hits)
    for idx, (fi, source), content_hash in to_parse:
        try:
            if parser is None:
                parser = ASTParser()
            parsed = parser.parse_file(fi, source)
        except Exception:
            continue
        merged[idx] = parsed
        if content_hash:
            _cache_parsed(parse_cache, parsed, content_hash)

    skipped = len(file_infos) - len(fi_and_bytes)  # unreadable files
    for idx, (fi, source) in enumerate(fi_and_bytes):
        parsed = merged.get(idx)
        if parsed is None:
            skipped += 1
            continue
        parsed_files.append(parsed)
        if collect_sources:
            source_map[fi.path] = source
        graph_builder.add_file(parsed)

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
    graph_builder.build()
    if parse_cache is not None:
        parse_cache.save()

    if skipped:
        log(f"[yellow]Skipped {skipped} file(s) that failed to parse.[/yellow]")

    # Add framework-aware synthetic edges (conftest, Django, FastAPI, Flask).
    try:
        from repowise.core.generation.editor_files.tech_stack import detect_tech_stack

        tech_items = detect_tech_stack(repo_path)
        fw_count = graph_builder.add_framework_edges([item.name for item in tech_items])
        if fw_count:
            log(f"Framework edges added: [cyan]{fw_count}[/cyan]")
    except Exception:
        pass  # framework edge detection is best-effort

    # Add dynamic-hint edges, mirroring the init pipeline's ingestion phase.
    # Without this the update-built graph was missing every dynamic edge the
    # init graph had: metrics computed on update diverged from init's, and
    # the first post-init update could never hit the centrality cache.
    try:
        from repowise.core.ingestion.dynamic_hints import HintRegistry

        dynamic_edges = HintRegistry().extract_all(
            Path(repo_path),
            dotnet_index=graph_builder.dotnet_index,
            file_paths=[fi.path for fi in file_infos],
        )
        graph_builder.add_dynamic_edges(dynamic_edges)
        if dynamic_edges:
            log(f"Dynamic hint edges added: [cyan]{len(dynamic_edges)}[/cyan]")
    except Exception:
        pass  # dynamic hints are best-effort, same as the init phase

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
    log: LogFn | None = None,
) -> tuple[list, dict[str, bytes], Any, Any, int, dict[str, dict]]:
    """Re-traverse + parse the repo, rebuild the graph (+ framework edges), and
    re-index git metadata for the changed files.

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

    Returns ``(parsed_files, source_map, graph_builder, repo_structure,
    file_count, git_meta_map)``.
    """
    log = log or _noop_log

    # Full re-ingest for graph (needed for cascade analysis)
    parsed_files, source_map, graph_builder, repo_structure, file_count = build_repo_graph(
        repo_path,
        exclude_patterns,
        collect_sources=True,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
        log=log,
    )

    # Re-index git metadata for changed files
    git_meta_map: dict[str, dict] = {}
    try:
        from repowise.core.ingestion.git_indexer import GitIndexer
        from repowise.core.ingestion.git_indexer.tiers import GitIndexTier

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
        )
        git_meta_map = {m["file_path"]: m for m in updated_meta}
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

    # Pre-compute centrality/community metrics with the init path's fan-out
    # parallelism. Without this, persist_graph_nodes computes the same
    # metrics lazily one-by-one. Runs after the co-change edge refresh so
    # the cached subgraphs reflect the final structure. Best-effort: every
    # metric still falls back to lazy computation.
    try:
        await graph_builder.compute_metrics_parallel()
    except Exception as exc:
        log(f"[yellow]Metric pre-computation skipped: {exc}[/yellow]")

    return parsed_files, source_map, graph_builder, repo_structure, file_count, git_meta_map


async def load_stored_git_meta(
    repo_path: Any, *, log: LogFn | None = None
) -> dict[str, dict] | None:
    """Read the persisted per-file git fields the dead-code analyzer scores on.

    Returns ``None`` when the store could not be read, and a mapping (possibly
    an empty one) when it could. The caller has to tell those apart: an empty
    mapping is a repository whose ``git_metadata`` genuinely holds nothing,
    which scores exactly the way a full index would score it, whereas a failed
    read means this run knows less than a full index does and must not
    overwrite what a full index wrote.

    Not every indexed file has a row even on a healthy repository — ``git
    metadata`` covers what ``index_repo`` produced, which excludes skipped
    paths — so a *missing file* is not evidence of a failure and a coverage
    count cannot stand in for one.
    """
    log = log or _noop_log
    # Never create the store as a side effect of reading it. A repo whose
    # wiki.db was deleted to force a rebuild would otherwise be handed an
    # empty one here, and an empty database reads as "indexed" downstream.
    # Same guard, and the same reason, as the head-commit stamper.
    if not (Path(repo_path) / ".repowise" / "wiki.db").is_file():
        log("[yellow]No store to read git metadata from; dead-code scope narrowed[/yellow]")
        return None
    try:
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_dead_code_git_fields,
            get_session,
        )
        from repowise.core.persistence.crud import get_repository_by_path
        from repowise.core.persistence.database import resolve_db_url

        engine = create_engine(resolve_db_url(repo_path))
        try:
            async with get_session(create_session_factory(engine)) as session:
                repo = await get_repository_by_path(session, str(repo_path))
                if repo is None:
                    # The persist path resolves the repository with an upsert,
                    # so a miss here means this run would be writing against a
                    # row that does not exist yet. Unknown, not empty.
                    log(
                        f"[yellow]No stored repository for {repo_path}; "
                        "dead-code scope narrowed[/yellow]"
                    )
                    return None
                return await get_dead_code_git_fields(session, repo.id)
        finally:
            await engine.dispose()
    except Exception as exc:
        log(f"[yellow]Stored git metadata unavailable for dead-code scoring: {exc}[/yellow]")
        return None


def run_partial_analysis(
    repo_path: Any,
    graph_builder: Any,
    git_meta_map: dict,
    parsed_files: list,
    file_diffs: list,
    *,
    source_map: dict[str, bytes] | None = None,
    stored_git_meta: dict[str, dict] | None = None,
    log: LogFn | None = None,
) -> tuple[Any, Any]:
    """Run partial code-health + repo-wide dead-code analysis.

    Returns ``(partial_health_report, dead_code_report)`` — either may be
    ``None`` if its analysis failed (both are best-effort).

    *source_map* is ingestion's ``{path: raw bytes}`` for this rebuild; the
    dead-code prepasses read it instead of re-reading the repo from disk.

    *stored_git_meta* is the persisted per-file git metadata, supplied to the
    dead-code analyzer *only*. ``git_meta_map`` holds this run's freshly
    indexed rows, which on an incremental update means the changed files and
    nothing else; every other file would otherwise be scored against an empty
    dict and land on the ``commit_count_90d == 0`` rung of the confidence
    ladder at 0.7 / ``safe_to_delete=True``. It is deliberately NOT merged into
    ``git_meta_map`` itself: the partial health analysis reads that map's
    entries as a repo-wide aggregate, so widening it there would silently move
    health scores (the same reason the idle-decay rows are kept out of it).

    ``None`` means the store could not be read, which is different from an
    empty mapping and narrows what the resulting report is allowed to
    overwrite. See ``load_stored_git_meta``.
    """
    log = log or _noop_log

    # Run partial code-health analysis up front so both the index-only
    # and full paths can upsert findings/metrics for changed files only.
    # The full file-list is needed because duplication is cross-file —
    # but only files in ``changed_paths`` produce new findings/metrics.
    partial_health_report = None
    try:
        from repowise.core.analysis.health import HealthAnalyzer
        from repowise.core.analysis.health.config import HealthConfig

        _health_analyzer = HealthAnalyzer(
            graph_builder.graph(),
            git_meta_map=git_meta_map,
            parsed_files=parsed_files,
            duplication_cache_dir=Path(repo_path) / ".repowise",
            repo_root=repo_path,
        )
        _health_changed = {fd.path for fd in file_diffs if fd.status in ("added", "modified")}
        if _health_changed:
            _hcfg = HealthConfig.load(repo_path)
            _analyzer_config = (
                _hcfg.to_analyzer_config([pf.file_info.path for pf in parsed_files])
                if _hcfg.has_overrides()
                else None
            )
            partial_health_report = _health_analyzer.analyze(
                _analyzer_config, changed_files=_health_changed
            )
            log(
                f"Health analysis (partial): [cyan]{len(_health_changed)} files[/cyan], "
                f"[yellow]{len(partial_health_report.findings)} findings[/yellow]"
            )
    except Exception as exc:
        log(f"[yellow]Health analysis skipped: {exc}[/yellow]")

    # Run dead-code analysis up front so both branches can persist its
    # results. Previously this sat below the ``if index_only``
    # short-circuit, which left the closure's reference to
    # ``dead_code_report`` unbound and crashed every ``--index-only`` run.
    dead_code_report = None
    try:
        from repowise.core.analysis.dead_code import DeadCodeAnalyzer

        # parsed_files enables the source-scan rescues (dynamic markers,
        # bundler aliases, export aliases) on the update path, matching init.
        #
        # This run's freshly indexed rows win over the stored ones for the
        # files they cover; the stored rows carry every other file, which is
        # what init's analyzer had and this path did not.
        _dead_code_git_meta = {**(stored_git_meta or {}), **git_meta_map}
        _dead_code_analyzer = DeadCodeAnalyzer(
            graph_builder.graph(),
            _dead_code_git_meta,
            parsed_files=graph_builder._parsed_files,
            source_map=source_map,
        )
        # Repo-wide, and persisted repo-wide. The detectors were always
        # repo-wide — the update path just discarded everything outside the
        # change set before writing, which is why an unchanged file that the
        # change had made dead (or brought back to life) kept its old verdict
        # until someone re-indexed from scratch.
        dead_code_report = _dead_code_analyzer.analyze()

        # What this report is allowed to overwrite.
        #
        # When the stored read succeeded, this run's git knowledge is the same
        # knowledge a full index had — the stored rows ARE what the last full
        # index wrote, with this run's fresher ones on top — so every file is
        # scored at least as well here as it was there and the report speaks
        # for the whole repository. That includes files with no git row at
        # all: a full index had no row for them either and scored them the
        # same way, so holding them back would protect nothing while leaving
        # exactly the stale verdicts this is meant to fix. Coverage is not the
        # test, and cannot be: ``git_metadata`` legitimately covers only the
        # files ``index_repo`` produced.
        #
        # When the read FAILED, this run knows strictly less than the index it
        # would be overwriting: every file outside the change set would be
        # scored against an empty dict, which reads as "no commits" and stores
        # 0.7 with ``safe_to_delete=True`` however active the file is. So it
        # speaks only for the files it re-indexed this run, which is the
        # behavior this path had before it was widened.
        dead_code_report.authoritative_paths = (
            None if stored_git_meta is not None else frozenset(git_meta_map)
        )
        if dead_code_report.authoritative_paths is not None:
            log(
                "[yellow]Dead-code findings limited to "
                f"{len(dead_code_report.authoritative_paths)} re-indexed files; "
                "the rest keep their previous verdict[/yellow]"
            )
        if dead_code_report.total_findings:
            log(f"Dead code findings: [yellow]{dead_code_report.total_findings}[/yellow]")
    except Exception as exc:
        log(f"[yellow]Dead code analysis skipped: {exc}[/yellow]")

    return partial_health_report, dead_code_report


async def refresh_knowledge_graph(
    repo_path: Any,
    parsed_files: list,
    graph_builder: Any,
    repo_structure: Any,
    git_meta_map: dict,
    dead_code_report: Any,
    *,
    prior_fingerprint: str | None,
    log: LogFn | None = None,
) -> Any | None:
    """Rebuild the KG skeleton + curation when the graph shape changed.

    The knowledge graph (layers, tour, entry points, curated node meta) was
    historically rebuilt only by the full init pipeline, so every incremental
    ``repowise update`` carried the init-time KG forward verbatim and agents
    read a stale orientation snapshot (#669). This reruns the deterministic
    skeleton + curation passes against the freshly rebuilt graph, then carries
    forward the prior artifact's LLM-enriched layer names and node summaries
    by stable id — so index-only updates stay LLM-free without regressing
    enrichment. LLM re-enrichment stays with the caller (docs mode only).

    Returns the refreshed result, or ``None`` when the graph fingerprint is
    unchanged (the persisted artifact is already current) or the rebuild
    failed (keep the prior artifact rather than export a broken one).
    """
    log = log or _noop_log
    try:
        from repowise.core.analysis.knowledge_graph import (
            KnowledgeGraphResult,
            build_knowledge_graph_skeleton,
            compute_kg_fingerprint,
            should_skip_kg_rebuild,
        )

        kg_json_path = Path(repo_path) / ".repowise" / "knowledge-graph.json"
        new_fingerprint = compute_kg_fingerprint(graph_builder)
        if should_skip_kg_rebuild(prior_fingerprint, new_fingerprint, kg_json_path):
            return None

        tech_stack: list[dict] = []
        try:
            from repowise.core.generation.editor_files.tech_stack import detect_tech_stack

            tech_stack = [
                {"name": t.name, "version": t.version, "category": t.category}
                for t in detect_tech_stack(repo_path)
            ]
        except Exception:
            pass  # tech stack is contextual metadata, not structural

        prior_kg = KnowledgeGraphResult.from_file(kg_json_path)

        kg = build_knowledge_graph_skeleton(
            parsed_files=parsed_files,
            graph_builder=graph_builder,
            repo_structure=repo_structure,
            tech_stack=tech_stack,
            external_systems=[],
            git_meta_map=git_meta_map,
            dead_code_report=dead_code_report,
            repo_path=Path(repo_path),
        )
        kg.fingerprint = new_fingerprint

        from repowise.core.analysis.kg_curation import (
            apply_summary_floor,
            curate_knowledge_graph,
            curation_enabled,
        )

        kg = curate_knowledge_graph(
            kg,
            parsed_files=parsed_files,
            graph_builder=graph_builder,
            repo_structure=repo_structure,
            community_info=graph_builder.community_info(),
            git_meta_map=git_meta_map,
            enabled=curation_enabled(),
            # Floor after the prior-artifact carry-forward below so carried
            # page-derived summaries win over the deterministic floor.
            defer_summary_floor=True,
        )

        if prior_kg is not None:
            _carry_forward_kg_enrichment(kg, prior_kg)

        # Summaries degrade to empty on failure, same as the init-path seam.
        import contextlib

        with contextlib.suppress(Exception):
            apply_summary_floor(kg, parsed_files)

        log(
            f"Knowledge graph refreshed: [cyan]{len(kg.layers)}[/cyan] layers, "
            f"[cyan]{len(kg.tour)}[/cyan] tour steps"
        )
        return kg
    except Exception as exc:
        log(f"[yellow]Knowledge-graph refresh skipped: {exc}[/yellow]")
        return None


def _carry_forward_kg_enrichment(kg: Any, prior_kg: Any) -> None:
    """Adopt the prior artifact's LLM-enriched prose onto the rebuilt KG.

    Matching is by stable id, and only fields the deterministic passes left
    empty are filled — structural changes always win over stale prose. Layer
    descriptions exist only after LLM enrichment (curation names layers but
    leaves descriptions empty), so a non-empty prior description is the
    signal that the prior name/description pair is the enriched one.
    """
    prior_layers = {layer.get("id"): layer for layer in prior_kg.layers or []}
    for layer in kg.layers or []:
        prior = prior_layers.get(layer.get("id"))
        if prior and prior.get("description") and not layer.get("description"):
            layer["name"] = prior.get("name") or layer.get("name")
            layer["description"] = prior["description"]

    prior_summaries = {n.get("id"): n["summary"] for n in prior_kg.nodes or [] if n.get("summary")}
    for node in kg.nodes or []:
        if not node.get("summary"):
            prior_summary = prior_summaries.get(node.get("id"))
            if prior_summary:
                node["summary"] = prior_summary

    # With curation disabled the skeleton carries no tour and only the LLM
    # path builds one — keep the prior tour rather than exporting none.
    if not kg.tour and prior_kg.tour:
        kg.tour = prior_kg.tour


async def _analyzed_commit(session: Any, repo_id: str) -> str | None:
    """Live HEAD of the repo being updated, for stamping health rows.

    Read off disk rather than from ``Repository.head_commit``: the health pass
    just scored the working tree, and the stored column is written by a
    different step whose ordering relative to this one is not guaranteed.
    ``None`` on any failure — an unstamped row reads as "not recorded", which
    is honest, while a wrong sha would not be.
    """
    from repowise.core.persistence.models import Repository
    from repowise.core.workspace.update import get_head_commit

    try:
        repo = await session.get(Repository, repo_id)
        local_path = getattr(repo, "local_path", None) if repo else None
        return get_head_commit(Path(local_path)) if local_path else None
    except Exception:
        return None


async def persist_partial_health(session: Any, repo_id: str, report: Any) -> None:
    """Upsert health findings + metrics for the changed-files subset.

    Unlike ``persist_pipeline_result`` (which delete-then-inserts the
    whole repo), this writer only touches rows whose ``file_path`` is in
    the partial report — so unchanged files keep their existing findings
    and metrics across an incremental ``repowise update``.
    """
    from repowise.core.persistence.crud import (
        upsert_health_findings,
        upsert_health_metrics,
        upsert_refactoring_suggestions,
    )

    changed_paths = sorted({m.file_path for m in report.metrics or []})
    if not changed_paths:
        return
    await upsert_health_metrics(
        session,
        repo_id,
        report.metrics or [],
        analyzed_commit=await _analyzed_commit(session, repo_id),
    )
    await upsert_health_findings(
        session, repo_id, list(report.findings or []), file_paths=changed_paths
    )
    # Refactoring suggestions for the changed files only (unchanged files keep
    # theirs). Scoped delete-then-insert across the full changed-file set, so a
    # file that became clean has its stale suggestions removed.
    await upsert_refactoring_suggestions(
        session,
        repo_id,
        list(getattr(report, "refactoring_suggestions", None) or []),
        file_paths=changed_paths,
    )
    # Per-function blame rollup for the changed files (keeps git_function_blame
    # current between full indexes; FULL git tier only — empty otherwise).
    fn_blame_rows = getattr(report, "function_blame_rows", None)
    if fn_blame_rows:
        from repowise.core.persistence.crud import upsert_git_function_blame_bulk

        await upsert_git_function_blame_bulk(session, repo_id, fn_blame_rows)


async def persist_incremental_commits(session: Any, repo_id: str, repo_path: Any) -> None:
    """Capture + upsert ``git_commits`` rows for commits new since the last index.

    Foundation 1 only populated the per-commit table on the full orchestrator
    index; without this, the commits/change-risk surface goes stale between full
    re-indexes. Bounds the walk to commits newer than the newest persisted
    ``committed_at`` (one ``git log`` pass) and upserts (idempotent on sha).
    """
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.persistence.crud import (
        get_latest_commit_committed_at,
        get_repository,
        update_repo_git_totals,
        upsert_git_commits_bulk,
    )
    from repowise.core.repo_config import load_repo_config

    cfg = load_repo_config(repo_path)
    indexer = GitIndexer(
        repo_path,
        commit_limit=cfg.get("commit_limit"),
        follow_renames=cfg.get("follow_renames", False),
        # The fix-event capture walks the tracked-file set itself, so without
        # the repo's excludes an update would store events for files a full
        # index never sees, and they would never age out.
        exclude_patterns=cfg.get("exclude_patterns"),
        # Git episodes ride the same capture and inherit those excludes.
        record_episodes=True,
    )
    newest = await get_latest_commit_committed_at(session, repo_id)
    since_ts: int | None = None
    if newest is not None:
        # SQLite drops tzinfo, so a naive read must be interpreted as UTC (the
        # column is stored tz-aware) rather than local time.
        from datetime import UTC

        dt = newest if newest.tzinfo is not None else newest.replace(tzinfo=UTC)
        since_ts = int(dt.timestamp())
    rows = await asyncio.to_thread(indexer.capture_new_commit_rows, since_ts=since_ts)
    if rows:
        await upsert_git_commits_bulk(session, repo_id, rows)

    await reconcile_commit_experience(session, repo_id, indexer)
    # Fills the commit-offset column on indexes written before it existed, so a
    # new capture never needs a re-index to become useful.
    await reconcile_commit_offsets(session, repo_id, indexer)

    # Refresh the repo-level whole-history totals so age / commit / contributor
    # counts keep growing between full re-indexes (#730). Cheap git calls, and
    # cheap to run every update since they don't touch the bounded sample —
    # except lifetime churn, which walks the history. Handing the capture what
    # was stored last time lets it add only the range since, and it re-proves
    # that range is safe to add before doing so.
    prior = _churn_prior(await get_repository(session, repo_id))
    totals = await asyncio.to_thread(indexer.capture_repo_totals, prior)
    await update_repo_git_totals(
        session,
        repo_id,
        total_commit_count=totals.total_commit_count,
        first_commit_at=totals.first_commit_at,
        total_contributor_count=totals.total_contributor_count,
        first_commit_author=totals.first_commit_author,
        first_commit_subject=totals.first_commit_subject,
        total_lines_added=totals.total_lines_added,
        total_lines_deleted=totals.total_lines_deleted,
        churn_anchor_sha=totals.churn_anchor_sha,
    )

    await persist_incremental_fix_events(session, repo_id, indexer)


def _churn_prior(repo_row: Any) -> Any:
    """The stored totals lifetime churn can resume from, or ``None``.

    Only the four fields the fold reads are carried across, so this never
    becomes a second way to read repo-level git facts. ``None`` for a repo row
    that is missing or has no anchor yet, which makes the capture walk the whole
    history exactly as it always did.
    """
    from repowise.core.ingestion.git_indexer.records import RepoTotals

    if repo_row is None:
        return None
    anchor = getattr(repo_row, "churn_anchor_sha", None)
    if not anchor:
        return None
    return RepoTotals(
        total_commit_count=repo_row.total_commit_count,
        total_lines_added=repo_row.total_lines_added,
        total_lines_deleted=repo_row.total_lines_deleted,
        churn_anchor_sha=anchor,
    )


async def reconcile_commit_experience(session: Any, repo_id: str, indexer: Any) -> None:
    """Re-tally author experience across the whole commit table and re-score.

    ``build_commit_rows`` can only count the commits it is handed. On a full
    index that is the entire window, so the number lands right; on an update it
    is just the commits newer than the last one persisted, so every author's
    count restarts at zero and an established author's new commits look like a
    first-timer's. That is not only a cosmetic badge: ``author_experience`` is a
    change-risk feature, so the stored ``change_risk_score`` inherits the error
    and the review queue ranks on it.

    Rather than seed the batch tally, this re-derives experience over the full
    persisted history after the append. The batch-local pass then has no lasting
    say, so there is no second code path to keep correct, and rows written by
    earlier (wrong) updates are repaired on the next one instead of waiting for
    a re-index.

    Two things have to happen together. Unreachable commits are dropped first:
    an update run on a feature branch persists that branch's shas, and once it
    is squash-merged they survive as orphans that no full index would produce
    and inflate every author's count. Then each surviving commit is re-scored
    from its stored features, because a corrected experience that leaves the old
    score in place would just move the inconsistency somewhere less visible.

    Bounded by the commit count and git-free apart from one ``rev-list``, and
    failure-isolated like the rest of the git-phase refreshes.
    """
    from repowise.core.persistence.crud import (
        delete_git_commits_by_sha,
        get_commit_experience_inputs,
        upsert_git_commits_bulk,
    )

    try:
        stored = await get_commit_experience_inputs(session, repo_id)
        if not stored:
            return

        reachable = await asyncio.to_thread(indexer.list_reachable_shas)
        if reachable is not None:
            orphans = [r["sha"] for r in stored if r["sha"] not in reachable]
            if orphans:
                await delete_git_commits_by_sha(session, repo_id, orphans)
                stored = [r for r in stored if r["sha"] in reachable]
                logger.info("commit_orphans_pruned", repo_id=repo_id, count=len(orphans))

        updates = _recompute_commit_experience(stored)
        if updates:
            await upsert_git_commits_bulk(session, repo_id, updates)
            logger.info("commit_experience_reconciled", repo_id=repo_id, count=len(updates))
    except Exception as exc:
        logger.debug("commit_experience_reconcile_failed", error=str(exc))


async def reconcile_commit_offsets(session: Any, repo_id: str, indexer: Any) -> None:
    """Backfill ``committed_offset_minutes`` on commits indexed before it existed.

    The offset is captured on the commit walk, so a full re-index gets it for
    free — but an update only writes rows for *new* commits, which would leave a
    repo indexed earlier with author-local hours on recent commits and UTC on
    everything older. A punch card mixing the two is worse than one that is
    honestly all-UTC, so the gap is closed here rather than waiting for a
    re-index nobody should have to run for a new column.

    One ``git log`` and one bulk update, both skipped entirely once the column
    is filled — the common case is a single cheap SELECT that returns nothing.
    Failure-isolated like the other update-time reconciles; an unfilled offset
    just means those rows keep falling back to UTC.
    """
    from repowise.core.persistence.crud import (
        get_commits_missing_offset,
        upsert_git_commits_bulk,
    )

    try:
        pending = await get_commits_missing_offset(session, repo_id)
        if not pending:
            return

        offsets = await asyncio.to_thread(indexer.capture_commit_offsets, pending)
        if not offsets:
            return

        await upsert_git_commits_bulk(
            session,
            repo_id,
            [{"sha": sha, "committed_offset_minutes": minutes} for sha, minutes in offsets.items()],
        )
        logger.info("commit_offsets_backfilled", repo_id=repo_id, count=len(offsets))
    except Exception as exc:
        logger.debug("commit_offset_reconcile_failed", error=str(exc))


def _committed_ts(committed_at: Any) -> float:
    """``committed_at`` as a sortable epoch, or 0 when the row has no date.

    SQLite drops tzinfo, so a naive read has to be reinterpreted as UTC — the
    column is stored tz-aware. Doing that first also keeps ``timestamp()`` off
    the platform's local-time conversion, which fails outright on Windows for
    pre-epoch dates.
    """
    if committed_at is None:
        return 0.0
    from datetime import UTC

    if committed_at.tzinfo is None:
        committed_at = committed_at.replace(tzinfo=UTC)
    return committed_at.timestamp()


def _recompute_commit_experience(stored: list[dict]) -> list[dict]:
    """Rows whose experience or risk moved, as partial upserts keyed by sha.

    Pure, so the ordering and re-scoring can be tested without a database.
    Emits only changed rows: on a settled index that is a handful, which keeps a
    whole-table read from turning into a whole-table write every update.
    """
    from repowise.core.analysis.change_risk import change_features_from_stored, score_change
    from repowise.core.ingestion.git_indexer.commit_rows import author_experience_by_sha

    exp_by_sha = author_experience_by_sha(
        [
            {
                "sha": r["sha"],
                "author_name": r["author_name"] or "",
                "author_email": r["author_email"] or "",
                # committed_at is the only ordering key the table stores; rows
                # without one sort oldest, which is where an unknown date least
                # disturbs everyone else's running count.
                "ts": _committed_ts(r["committed_at"]),
            }
            for r in stored
        ]
    )

    updates: list[dict] = []
    for r in stored:
        exp = exp_by_sha.get(r["sha"], 0)
        risk = score_change(
            change_features_from_stored(
                la=r["lines_added"],
                ld=r["lines_deleted"],
                nf=r["files_changed"],
                nd=r["dirs_changed"],
                ns=r["subsystems_changed"],
                entropy=r["entropy"],
                exp=exp,
                is_fix=r["is_fix"],
                author=r["author_name"],
                subject=r["subject"],
                ref=r["sha"],
            )
        )
        unchanged = (
            r["author_experience"] == exp
            and r["change_risk_level"] == risk.level
            and r["change_risk_score"] is not None
            and abs(r["change_risk_score"] - risk.score) < 1e-9
        )
        if unchanged:
            continue
        updates.append(
            {
                "sha": r["sha"],
                "author_experience": exp,
                "change_risk_score": risk.score,
                "change_risk_level": risk.level,
            }
        )
    return updates


async def persist_incremental_fix_events(session: Any, repo_id: str, indexer: Any) -> None:
    """Trace + upsert ``fix_events`` for fix commits this index has not seen.

    Two halves, and both are needed for an update to converge on what a fresh
    index would produce: append the fix commits missing from the table, then drop
    the ones that have aged out of the trailing defect window. Without the prune
    the stored set only ever grows past the window's trailing edge; without the
    append it goes stale. Failure-isolated, like every other git-phase refresh.
    """
    from repowise.core.persistence.crud import (
        get_fix_event_shas,
        prune_fix_events_before,
        prune_fix_events_for_missing_paths,
        upsert_fix_events_bulk,
    )

    try:
        known = await get_fix_event_shas(session, repo_id)
        rows, oldest_ts, tracked = await asyncio.to_thread(
            indexer.capture_new_fix_events, known_shas=known
        )
    except Exception as exc:
        logger.debug("incremental_fix_events_failed", error=str(exc))
        return

    if rows:
        await upsert_fix_events_bulk(session, repo_id, rows)
    # A zero cutoff means the walk or the trace failed; pruning then would drop
    # rows nothing replaced.
    if oldest_ts:
        from datetime import UTC, datetime

        await prune_fix_events_before(session, repo_id, datetime.fromtimestamp(oldest_ts, tz=UTC))
    if tracked:
        await prune_fix_events_for_missing_paths(session, repo_id, tracked)

    # Recompute over the whole stored window, not just the rows this update
    # appended: decay ages every file's mass whether or not the file changed.
    try:
        from repowise.core.pipeline.fix_rollups import apply_fix_rollups

        await apply_fix_rollups(session, repo_id)
    except Exception as exc:
        logger.debug("fix_rollups_failed", error=str(exc))


async def refresh_external_systems(
    session: Any,
    repo_id: str,
    repo_path: Any,
    file_diffs: list,
    *,
    log: LogFn | None = None,
) -> bool:
    """Re-extract + reconcile external systems (C4 L1) when a manifest changed.

    The incremental path historically never refreshed the ``external_systems``
    table, so the C4 architecture panel served the init-time dependency list
    until the next full re-index. Extraction is a bounded repo walk (no LLM, no
    network), so it is gated on an actual dependency-manifest change in this
    update's diff — the common no-manifest update pays nothing.

    When a manifest did change, the *whole* repo is re-extracted: it's cheap,
    and only a complete set supports a clean reconcile that also drops deps
    removed from a manifest. The table is then replaced (removed deps pruned)
    and ``external:{name}`` graph nodes re-linked so a brand-new dependency's
    node isn't left with a NULL FK.

    Ceiling: re-extraction walks the repo (depth ≤ 4) rather than parsing only
    the changed manifests — fine because it runs only on a manifest change and
    a partial parse can't see cross-manifest removals. Returns ``True`` when a
    refresh actually ran.
    """
    log = log or _noop_log
    from repowise.core.ingestion.external_systems import is_manifest_path

    if not any(is_manifest_path(fd.path) for fd in file_diffs or []):
        return False

    from repowise.core.ingestion.external_systems import extract_external_systems

    records = await asyncio.to_thread(extract_external_systems, Path(repo_path))
    systems = [
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

    from repowise.core.persistence.crud import (
        link_graph_nodes_to_external_systems,
        replace_external_systems,
    )

    id_map = await replace_external_systems(session, repo_id, systems)
    # Collapse multi-manifest duplicates: any id for a given name works (the
    # C4 renderer only needs name/category/ecosystem, stable across rows).
    name_to_id: dict[str, int] = {}
    for (name, _declared_in), sys_id in id_map.items():
        name_to_id.setdefault(name, sys_id)
    await link_graph_nodes_to_external_systems(session, repo_id, name_to_id)
    log(f"External systems refreshed: [cyan]{len(systems)}[/cyan] deps")
    return True


async def persist_incremental_index(
    repo_path: Any,
    graph_builder: Any,
    git_meta_map: dict,
    dead_code_report: Any,
    partial_health_report: Any,
    changed_paths: list[str],
    *,
    current_graph_file_paths: set[str] | None = None,
    file_diffs: list[Any] | None = None,
    knowledge_graph_result: Any | None = None,
    parsed_files: list[Any] | None = None,
    git_decay_map: dict | None = None,
    log: LogFn | None = None,
    degraded: list[str] | None = None,
    failed_steps: list[str] | None = None,
) -> None:
    """Persist an incremental index refresh (graph + symbols + git + dead-code + health).

    Upsert-only: unchanged files keep their existing rows, unlike
    ``persist_pipeline_result``'s delete-then-insert. State-file updates stay
    with the caller — this writes the DB only.

    ``degraded`` (when supplied) collects a one-line entry for every
    best-effort step that failed, so the caller can render an honest
    completion report instead of silently claiming success.

    ``failed_steps`` (when supplied) collects the names of the failed steps
    whose input was *this commit range* rather than the whole repo. Those are
    the only failures a later run cannot heal on its own: their data is scoped
    to a range the sync pointer is about to move past, so the caller records
    them as a repair marker and re-covers the range next update.

    The test for membership is "would widening the next run's diff base repair
    this, and would nothing else". Steps that re-derive the whole repo every
    run are deliberately absent (graph nodes, the sweeps, the page tree,
    related pages, the knowledge graph, the deleted-file prune): they heal
    themselves on the next update, and marking them would let a permanently
    broken one pin the repair window open. So is the commit capture, which
    looks range-scoped and is not: it bounds its walk by the newest
    ``committed_at`` already in the table, so a run it skipped is re-walked by
    the next one whatever the diff base says.
    """
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.database import resolve_db_url

    log = log or _noop_log

    def _skip(step: str, exc: Exception, *, range_scoped: bool = False) -> None:
        log(f"[yellow]{step} skipped: {exc}[/yellow]")
        if degraded is not None:
            degraded.append(f"{step}: {exc}")
        if range_scoped and failed_steps is not None:
            failed_steps.append(step)

    url = resolve_db_url(repo_path)
    engine = create_engine(url)
    # Filled by the tombstone step; read after the session closes, so it has to
    # survive a step that was skipped.
    tombstoned_page_ids: list[str] = []
    # Same contract, for rows of a page that has been retired outright.
    swept_page_ids: list[str] = []
    try:
        await init_db(engine)
        sf = create_session_factory(engine)

        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
            repo_id = repo.id


            # Delete rows of pages retired since this index was built. This
            # path never regenerates a repo-wide page, so nothing else here
            # would ever visit one to notice it should be gone, and for a user
            # whose updates all come from the post-commit hook this is the only
            # place a retirement can land.
            try:
                from repowise.core.pipeline.persist import (
                    sweep_absent_cycle_pages,
                    sweep_retired_pages,
                )

                swept_page_ids = await sweep_retired_pages(session, repo_id)
                # Same reasoning as the retirement sweep above: this path never
                # regenerates a cycle page, so asking the rebuilt graph whether
                # the cycle still exists is the only way a fixed cycle's page
                # can ever be retired for a user who only runs `update`.
                swept_page_ids += await sweep_absent_cycle_pages(
                    session, repo_id, graph_builder
                )
            except Exception as exc:
                _skip("Retired page sweep", exc)

            # Tombstone pages for deleted/renamed files FIRST — a fresh page
            # for a file that no longer exists misleads every retrieval
            # consumer until the next full regeneration.
            if file_diffs:
                try:
                    from repowise.core.pipeline.persist import (
                        mark_tombstone_pages,
                        tombstone_candidates,
                    )

                    tombstoned_page_ids = await mark_tombstone_pages(
                        session, repo_id, tombstone_candidates(file_diffs)
                    )
                except Exception as exc:
                    _skip("Tombstone marking", exc, range_scoped=True)

            # Placement depends on the whole page set, which on an incremental
            # run lives in the store rather than in the pages just generated.
            try:
                from repowise.core.pipeline.page_tree_sync import rebuild_page_tree

                await rebuild_page_tree(session, repo_id)
            except Exception as exc:
                _skip("Page tree rebuild", exc)

            if git_meta_map or git_decay_map:
                try:
                    from repowise.core.persistence.crud import (
                        recompute_git_percentiles,
                        upsert_git_metadata_bulk,
                    )

                    # Idle files' decay-only rows upsert alongside the changed
                    # files' full rows; the percentile re-rank then runs over
                    # every row against the freshly decayed scores (#728).
                    await upsert_git_metadata_bulk(
                        session,
                        repo_id,
                        [*git_meta_map.values(), *(git_decay_map or {}).values()],
                    )
                    await recompute_git_percentiles(session, repo_id)
                except Exception as exc:
                    _skip("Git persist", exc, range_scoped=True)

                try:
                    await persist_incremental_commits(session, repo_id, repo_path)
                except Exception as exc:
                    _skip("Commit capture", exc)

            if dead_code_report is not None:
                try:
                    from repowise.core.persistence.crud import (
                        replace_dead_code_findings,
                    )

                    # The report is repo-wide, and dead code is a cross-file
                    # property, so a change-scoped write would drop every
                    # verdict the change flipped outside the change set. The
                    # scope is the set of files whose confidence was scored on
                    # real git metadata; the rest keep what they had.
                    await replace_dead_code_findings(
                        session,
                        repo_id,
                        dead_code_report.findings,
                        scope=dead_code_report.authoritative_paths,
                    )
                except Exception as exc:
                    _skip("Dead-code persist", exc, range_scoped=True)

            if partial_health_report is not None:
                try:
                    await persist_partial_health(session, repo_id, partial_health_report)
                except Exception as exc:
                    _skip("Health persist", exc, range_scoped=True)

            # Re-persist graph_nodes so symbol-level PageRank /
            # betweenness / community ids stay in sync with the
            # current graph build. Without this, ``repowise update``
            # leaves stale per-symbol metrics from the original init
            # and the UI shows "Not indexed in graph" for every
            # symbol on existing repos.
            try:
                from repowise.core.pipeline.persist import persist_graph_nodes

                await persist_graph_nodes(session, repo_id, graph_builder)
            except Exception as exc:
                _skip("Graph nodes persist", exc)

            # Refresh wiki_symbols for the changed files. Historically the
            # incremental path re-parsed but never persisted symbols, so their
            # start/end bounds fossilized at the last full index and the
            # get_answer hydrator served drifted signatures. Scoped to the
            # changed set for cost.
            try:
                from repowise.core.pipeline.persist import persist_incremental_symbols

                await persist_incremental_symbols(session, repo_id, parsed_files, changed_paths)
            except Exception as exc:
                _skip("Symbol persist", exc, range_scoped=True)

            # Refresh graph_edges for the changed files. The full-init path was
            # historically the only writer of edges, so adjacency froze at the
            # last full index: new imports/calls stayed invisible and dropped
            # ones lingered as false paths. Phase E flow-path traversal reads
            # adjacency straight from this table, so it decayed on every update.
            try:
                from repowise.core.pipeline.persist import persist_incremental_edges

                await persist_incremental_edges(
                    session, repo_id, graph_builder, parsed_files, changed_paths
                )
            except Exception as exc:
                _skip("Graph edges persist", exc, range_scoped=True)

            # Refresh related-pages metadata across the whole wiki. LLM-free,
            # so even index-only updates heal pages generated before the
            # feature shipped (or drifted by new imports) in one run.
            try:
                from repowise.core.generation.related_pages import file_import_edges
                from repowise.core.persistence.crud import backfill_related_pages

                changed_rel = await backfill_related_pages(
                    session,
                    repo_id,
                    import_edges=file_import_edges(graph_builder),
                    git_meta_map=git_meta_map,
                    pagerank=graph_builder.pagerank(),
                )
                if changed_rel:
                    log(f"Related pages refreshed on {changed_rel} pages")
            except Exception as exc:
                _skip("Related-pages backfill", exc)

            if knowledge_graph_result is not None:
                try:
                    from repowise.core.pipeline.persist import persist_kg

                    await persist_kg(knowledge_graph_result, session, repo_id)
                except Exception as exc:
                    _skip("Knowledge-graph persist", exc)

            # Refresh external systems (C4 L1) when a dependency manifest
            # changed — otherwise the architecture panel serves the init-time
            # dep list forever. Gated + no LLM (see refresh_external_systems).
            if file_diffs:
                try:
                    await refresh_external_systems(session, repo_id, repo_path, file_diffs, log=log)
                except Exception as exc:
                    _skip("External systems refresh", exc, range_scoped=True)

            # One-shot drain of proposals from the removed code_comment
            # harvest (#751). Runs on the index-only path too, because the
            # post-commit hook's updates never reach the full decision
            # persist. Confirmed/dismissed rows are kept.
            try:
                from repowise.core.persistence.crud import (
                    purge_proposed_decisions_by_source,
                )

                await purge_proposed_decisions_by_source(session, repo_id, "code_comment")
            except Exception as exc:
                _skip("Decision purge", exc)

            # Drop file-scoped rows for files that have actually been deleted.
            # Without this an incremental update tombstones the deleted file's
            # page and leaves everything else: graph nodes, edges, metrics,
            # symbols, health rows and git metadata all keep serving a file
            # that is gone. The liveness question is asked of the filesystem
            # and git, never of this run's parse, so a transient read failure
            # cannot masquerade as a deletion (see prune_deleted_file_rows).
            #
            # Last in the session on purpose. Everything above upserts rather
            # than deleting, and the git step in particular indexes the changed
            # *paths*, which includes the deleted ones: pruning first left a
            # fresh git_metadata row for every file this run watched disappear.
            # Running last means the prune has the final say on what the store
            # claims exists.
            try:
                from repowise.core.pipeline.persist import prune_deleted_file_rows

                live_hint = set(
                    current_graph_file_paths
                    if current_graph_file_paths is not None
                    else {pf.file_info.path for pf in parsed_files or []}
                )
                # Plus every file node the rebuilt graph still holds, which is
                # the only reliable way to know a node names no file at all.
                # Framework and resolver passes mint file-shaped nodes for
                # things that were never on disk: `external:` imports,
                # `framework:` anchors, and Spring's `META-INF/services/<iface>`
                # SPI source, which carries no prefix to recognise it by. All
                # of them fail every liveness test there is, so a prune that
                # asked only about disk and git would delete them and take
                # their edges with them, and only *changed* files' edges are
                # rebuilt afterwards. A node this run's graph build still
                # contains is live by construction, whatever it names.
                #
                # Deliberately not guarded: a graph this run cannot read is a
                # run that has no business deciding what was deleted, and the
                # outer handler skips the prune entirely.
                graph = graph_builder.graph()
                live_hint |= {
                    node
                    for node, data in graph.nodes(data=True)
                    if data.get("node_type", "file") == "file"
                }
                pruned, refusals = await prune_deleted_file_rows(
                    session, repo_id, repo_path, live_hint=live_hint
                )
                if pruned:
                    log(f"Pruned rows for [cyan]{pruned}[/cyan] deleted file(s)")
                for refusal in refusals:
                    log(f"[yellow]{refusal}[/yellow]")
                    if degraded is not None:
                        degraded.append(refusal)
            except Exception as exc:
                _skip("Deleted-file prune", exc)

        # After the session closes: on SQLite the full-text index shares the
        # database file, so writing to it while the session holds a write lock
        # raises "database is locked".
        #
        # A tombstone can never be an answer — hydration drops it — but
        # retrieval fetches a fixed number of rows before that check runs, so
        # every tombstone left in the index costs a real candidate its slot.
        #
        # A swept page's FTS row is worse than a tombstone: search hydrates
        # title and snippet from the FTS copy itself, so an orphan answers in
        # full while the page it names 404s.
        if tombstoned_page_ids or swept_page_ids:
            try:
                from repowise.core.persistence.search import FullTextSearch

                fts = FullTextSearch(engine)
                await fts.ensure_index()
                if tombstoned_page_ids:
                    await fts.delete_many(tombstoned_page_ids)
                if swept_page_ids:
                    await fts.delete_many(swept_page_ids)
            except Exception as exc:
                # Range-scoped for the tombstone half: mark_tombstone_pages
                # re-marks and re-returns pages that are already tombstones, so
                # a widened re-run regenerates these ids and retries the delete.
                # The swept half is not repairable this way, because the sweeps
                # deleted those rows and a later run finds nothing to sweep.
                # Tagging still recovers strictly more than not tagging.
                _skip("Tombstone full-text removal", exc, range_scoped=True)

        # Ceiling: the swept pages' *vector* embeddings survive this path.
        # There is no store here to delete them from, and building one would
        # pull the lancedb import onto the post-commit hook, which
        # ``deterministic.py`` avoids on purpose — and with the default mock
        # embedder this path never wrote a page embedding in the first place.
        # LanceDB hydrates a hit from its own columns, so a residual embedding
        # can still surface in semantic search until the next docs-mode update
        # (which does delete it) or a reindex.
    finally:
        await engine.dispose()
