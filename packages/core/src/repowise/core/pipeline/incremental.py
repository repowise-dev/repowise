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
            Path(repo_path), dotnet_index=graph_builder.dotnet_index
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
    log: LogFn | None = None,
) -> tuple[list, dict[str, bytes], Any, Any, int, dict[str, dict]]:
    """Re-traverse + parse the repo, rebuild the graph (+ framework edges), and
    re-index git metadata for the changed files.

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


def run_partial_analysis(
    repo_path: Any,
    graph_builder: Any,
    git_meta_map: dict,
    parsed_files: list,
    file_diffs: list,
    *,
    log: LogFn | None = None,
) -> tuple[Any, Any]:
    """Run partial code-health + dead-code analysis for the changed files.

    Returns ``(partial_health_report, dead_code_report)`` — either may be
    ``None`` if its analysis failed (both are best-effort).
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

    # Run partial dead-code analysis up front so both branches can
    # persist its results. Previously this sat below the ``if index_only``
    # short-circuit, which left the closure's reference to
    # ``dead_code_report`` unbound and crashed every ``--index-only`` run.
    dead_code_report = None
    try:
        from repowise.core.analysis.dead_code import DeadCodeAnalyzer

        # parsed_files enables the source-scan rescues (dynamic markers,
        # bundler aliases, export aliases) on the update path, matching init.
        _analyzer_partial = DeadCodeAnalyzer(
            graph_builder.graph(), git_meta_map, parsed_files=graph_builder._parsed_files
        )
        _changed_paths_partial = [fd.path for fd in file_diffs]
        dead_code_report = _analyzer_partial.analyze_partial(_changed_paths_partial)
        if dead_code_report.total_findings:
            log(f"Dead code findings (partial): [yellow]{dead_code_report.total_findings}[/yellow]")
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
    await upsert_health_metrics(session, repo_id, report.metrics or [])
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
        update_repo_git_totals,
        upsert_git_commits_bulk,
    )
    from repowise.core.repo_config import load_repo_config

    cfg = load_repo_config(repo_path)
    indexer = GitIndexer(
        repo_path,
        commit_limit=cfg.get("commit_limit"),
        follow_renames=cfg.get("follow_renames", False),
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

    # Refresh the repo-level whole-history totals so age / commit / contributor
    # counts keep growing between full re-indexes (#730). Cheap git calls, and
    # cheap to run every update since they don't touch the bounded sample.
    totals = await asyncio.to_thread(indexer.capture_repo_totals)
    await update_repo_git_totals(
        session,
        repo_id,
        total_commit_count=totals.total_commit_count,
        first_commit_at=totals.first_commit_at,
        total_contributor_count=totals.total_contributor_count,
        first_commit_author=totals.first_commit_author,
    )


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
    log: LogFn | None = None,
    degraded: list[str] | None = None,
) -> None:
    """Persist an incremental index refresh (graph + symbols + git + dead-code + health).

    Upsert-only: unchanged files keep their existing rows, unlike
    ``persist_pipeline_result``'s delete-then-insert. State-file updates stay
    with the caller — this writes the DB only.

    ``degraded`` (when supplied) collects a one-line entry for every
    best-effort step that failed, so the caller can render an honest
    completion report instead of silently claiming success.
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

    def _skip(step: str, exc: Exception) -> None:
        log(f"[yellow]{step} skipped: {exc}[/yellow]")
        if degraded is not None:
            degraded.append(f"{step}: {exc}")

    url = resolve_db_url(repo_path)
    engine = create_engine(url)
    try:
        await init_db(engine)
        sf = create_session_factory(engine)

        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
            repo_id = repo.id

            if current_graph_file_paths:
                try:
                    from repowise.core.pipeline.persist import _prune_stale_file_rows

                    await _prune_stale_file_rows(session, repo_id, current_graph_file_paths, set())
                except Exception as exc:
                    _skip("Stale row prune", exc)

            # Tombstone pages for deleted/renamed files FIRST — a fresh page
            # for a file that no longer exists misleads every retrieval
            # consumer until the next full regeneration.
            if file_diffs:
                try:
                    from repowise.core.pipeline.persist import (
                        mark_tombstone_pages,
                        tombstone_candidates,
                    )

                    await mark_tombstone_pages(session, repo_id, tombstone_candidates(file_diffs))
                except Exception as exc:
                    _skip("Tombstone marking", exc)

            if git_meta_map:
                try:
                    from repowise.core.persistence.crud import (
                        recompute_git_percentiles,
                        upsert_git_metadata_bulk,
                    )

                    await upsert_git_metadata_bulk(session, repo_id, list(git_meta_map.values()))
                    await recompute_git_percentiles(session, repo_id)
                except Exception as exc:
                    _skip("Git persist", exc)

                try:
                    await persist_incremental_commits(session, repo_id, repo_path)
                except Exception as exc:
                    _skip("Commit capture", exc)

            if dead_code_report is not None:
                try:
                    from repowise.core.persistence.crud import (
                        upsert_dead_code_findings,
                    )

                    await upsert_dead_code_findings(
                        session, repo_id, dead_code_report.findings, file_paths=changed_paths
                    )
                except Exception as exc:
                    _skip("Dead-code persist", exc)

            if partial_health_report is not None:
                try:
                    await persist_partial_health(session, repo_id, partial_health_report)
                except Exception as exc:
                    _skip("Health persist", exc)

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
                _skip("Symbol persist", exc)

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
                _skip("Graph edges persist", exc)

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
    finally:
        await engine.dispose()
