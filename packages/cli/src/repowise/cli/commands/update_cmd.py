"""``repowise update`` — incremental wiki regeneration for changed files."""

from __future__ import annotations

import time

import click
from rich.table import Table

from repowise.cli.helpers import (
    CommandTarget,
    acquire_update_lock,
    console,
    ensure_repowise_dir,
    find_workspace_root,
    get_head_commit,
    load_config,
    load_state,
    release_update_lock,
    resolve_command_target,
    resolve_provider,
    resolve_reasoning,
    resolve_repo_path,
    run_async,
    save_state,
)

# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------


def _infer_legacy_docs_enabled(state: dict) -> bool:
    """Infer ``docs_enabled`` for state files written before the field existed.

    Pre-migration ``init`` only wrote ``provider`` / ``model`` to state when
    docs were generated; index-only init wrote nothing past ``last_sync_commit``.
    So absence of both fields is a reliable signal that the original run was
    index-only and we should default new updates to index-only too — this
    avoids surprising those users with a full LLM regen on first upgrade.
    Full-init users keep the old default (full mode) because their state
    has ``provider`` and ``model`` populated.
    """
    if state.get("provider") or state.get("model"):
        return True
    return False


def _resolve_index_only_mode(
    *,
    index_only: bool,
    docs_flag: bool | None,
    state: dict,
) -> bool:
    """Decide whether this update should skip LLM regeneration.

    Priority: explicit ``--index-only`` flag > ``--docs/--no-docs`` >
    ``state.docs_enabled`` > inferred default from legacy state shape.
    Encapsulated as a pure function so the post-commit hook does the right
    thing without needing any extra knobs at install time.
    """
    if index_only:
        return True
    if docs_flag is False:
        return True
    if docs_flag is True:
        return False
    # No explicit override — read state, falling back to a shape-based
    # inference for state files predating the docs_enabled field.
    if "docs_enabled" in state:
        return state["docs_enabled"] is False
    return _infer_legacy_docs_enabled(state) is False


# ---------------------------------------------------------------------------
# Workspace update flow
# ---------------------------------------------------------------------------


def _workspace_update(
    target: "CommandTarget",
    *,
    dry_run: bool = False,
) -> None:
    """Update stale repos in a workspace.

    Takes a resolved :class:`CommandTarget` so the caller has full control
    over how the workspace was located (auto-detected vs explicit flag).
    """
    from repowise.core.workspace import check_repo_staleness, update_workspace

    ws_root = target.ws_root
    ws_config = target.ws_config
    repo_alias = target.repo_filter
    if ws_root is None or ws_config is None:
        # Defensive: callers should always pass a workspace-mode target,
        # but guard against misuse so the error message is clear.
        raise click.ClickException("_workspace_update called without a workspace target.")

    # Show staleness summary first
    console.print(f"[bold]repowise update[/bold] — workspace: {ws_root.name}")
    console.print()

    stale_count = 0
    for entry in ws_config.repos:
        if repo_alias and entry.alias != repo_alias:
            continue
        abs_path = (ws_root / entry.path).resolve()
        stored = entry.last_commit_at_index
        is_stale, head, behind = check_repo_staleness(abs_path, stored)
        status = f"[yellow]{behind} new commit(s)[/yellow]" if is_stale else "[green]up to date[/green]"
        if not (abs_path / ".repowise").is_dir():
            status = "[dim]not indexed[/dim]"
        console.print(f"  {entry.alias:<20} {status}")
        if is_stale:
            stale_count += 1

    console.print()

    if stale_count == 0:
        console.print("[green]All repos are up to date.[/green]")
        return

    if dry_run:
        console.print(f"[yellow]Dry run — {stale_count} repo(s) would be updated.[/yellow]")
        return

    # Run the updates
    def _on_start(alias: str) -> None:
        console.print(f"  Updating [bold]{alias}[/bold]...")

    def _on_done(result: "RepoUpdateResult") -> None:
        if result.error:
            console.print(f"    [red]\u2717 {result.alias}: {result.error}[/red]")
        elif result.updated:
            console.print(
                f"    [green]\u2713[/green] {result.alias}: "
                f"{result.file_count} files, {result.symbol_count:,} symbols"
            )

    from repowise.core.workspace import RepoUpdateResult

    results = run_async(
        update_workspace(
            ws_root,
            ws_config,
            repo_filter=repo_alias,
            dry_run=False,
            on_repo_start=_on_start,
            on_repo_done=_on_done,
        )
    )

    # Summary
    updated = sum(1 for r in results if r.updated)
    errors = sum(1 for r in results if r.error)
    skipped = sum(1 for r in results if r.skipped_reason)
    console.print()
    console.print(
        f"[bold]Done:[/bold] {updated} updated, {skipped} skipped"
        + (f", {errors} errors" if errors else "")
    )


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("update")
@click.argument("path", required=False, default=None)
@click.option("--provider", "provider_name", default=None, help="LLM provider name.")
@click.option("--model", default=None, help="Model identifier override.")
@click.option("--since", default=None, help="Base git ref to diff from (overrides state).")
@click.option("--concurrency", type=int, default=5, help="Max concurrent LLM calls.")
@click.option(
    "--reasoning",
    type=click.Choice(["auto", "off", "minimal"]),
    default=None,
    help="Reasoning mode for supported providers: auto, off, or minimal.",
)
@click.option(
    "--cascade-budget",
    type=int,
    default=None,
    help="Max pages to regenerate (auto-scaled if unset).",
)
@click.option(
    "--dry-run", is_flag=True, default=False, help="Show affected pages without regenerating."
)
@click.option(
    "--workspace",
    "-w",
    is_flag=True,
    default=False,
    help="Force workspace mode (update all stale repos in the workspace).",
)
@click.option(
    "--no-workspace",
    is_flag=True,
    default=False,
    help="Force single-repo mode even when invoked from a workspace.",
)
@click.option(
    "--repo",
    "repo_alias",
    default=None,
    help="Update only this repo alias within the workspace (implies --workspace).",
)
@click.option(
    "--index-only",
    is_flag=True,
    default=False,
    help=(
        "Skip LLM page regeneration. Re-parses files, rebuilds the dependency "
        "graph, and refreshes git/dead-code artifacts only. Useful for tight "
        "iteration on extractors / resolvers without spending tokens."
    ),
)
@click.option(
    "--docs/--no-docs",
    "docs_flag",
    default=None,
    help=(
        "Override the persisted init mode for this run. --no-docs is "
        "equivalent to --index-only; --docs forces LLM page regeneration "
        "even when the repo was indexed without docs. Without either flag, "
        "the value of `docs_enabled` from .repowise/state.json is used "
        "(set during `repowise init`)."
    ),
)
def update_command(
    path: str | None,
    provider_name: str | None,
    model: str | None,
    since: str | None,
    reasoning: str | None,
    cascade_budget: int | None,
    dry_run: bool,
    workspace: bool,
    no_workspace: bool,
    repo_alias: str | None,
    index_only: bool = False,
    docs_flag: bool | None = None,
    concurrency: int = 5,
) -> None:
    """Incrementally update wiki pages for files changed since last sync.

    Auto-detects workspace mode when invoked from a workspace root or when a
    workspace exists upstream of the working directory. Use --no-workspace to
    force single-repo mode and --workspace to force workspace mode.
    """
    start = time.monotonic()

    # --- Resolve target up front (single repo or workspace) ---
    target = resolve_command_target(
        path=path,
        workspace_flag=workspace,
        no_workspace_flag=no_workspace,
        repo_alias=repo_alias,
    )
    target.notice(console, command="update")

    if target.is_workspace:
        _workspace_update(target, dry_run=dry_run)
        return

    # --- Single-repo path from here on. ---
    repo_path = target.repo_path
    assert repo_path is not None  # single mode always sets repo_path
    ensure_repowise_dir(repo_path)

    # Load saved API keys from .repowise/.env (won't overwrite existing env vars)
    from repowise.cli.ui import load_dotenv

    load_dotenv(repo_path)

    state = load_state(repo_path)
    base_ref = since or state.get("last_sync_commit")
    head = get_head_commit(repo_path)

    if base_ref is None:
        # Helpful diagnostic when the user landed here from a workspace
        # directory that was never indexed as a single repo. The auto-detect
        # path normally catches this earlier, but --no-workspace or an
        # explicit path can still route us here.
        hint = ""
        upstream_ws = find_workspace_root(repo_path)
        if upstream_ws is not None:
            hint = (
                f"\nA workspace was detected at {upstream_ws}. "
                "Did you mean: repowise update --workspace?"
            )
        raise click.ClickException(
            f"No previous sync found for {repo_path}. "
            "Run 'repowise init' there first, or pass --since <ref>."
            + hint
        )

    if head and head == base_ref:
        console.print("[green]Already up to date.[/green]")
        return

    # Backfill docs_enabled on legacy state files using the same
    # shape-based inference the resolver uses, so the post-commit hook
    # and future runs stop relying on the inference. Done before mode
    # resolution and only when no explicit override was passed, so a
    # one-off `--docs` / `--no-docs` doesn't lock anything in.
    explicit_override = (
        # The CLI default of index_only is False; treat it like an
        # override only when the user actually passed the flag, which we
        # can't distinguish here — but the conservative choice is fine:
        # index_only=True is always treated as an override.
        index_only or docs_flag is not None
    )
    if "docs_enabled" not in state and not explicit_override:
        state["docs_enabled"] = _infer_legacy_docs_enabled(state)
        save_state(repo_path, state)

    # --- Resolve effective mode (index-only vs full LLM regen) ---
    index_only = _resolve_index_only_mode(
        index_only=index_only, docs_flag=docs_flag, state=state
    )

    # --- Acquire update lock so the augment hook can suppress its
    # stale-wiki warning while this run is in flight (typical case: the
    # post-commit hook fires `repowise update` in the background, then a
    # follow-on tool call would otherwise warn that HEAD has moved). ---
    import atexit

    acquire_update_lock(repo_path, head)
    atexit.register(release_update_lock, repo_path)

    console.print(f"[bold]repowise update[/bold] — {repo_path}")
    console.print(f"Diffing [cyan]{base_ref[:8]}..{(head or 'HEAD')[:8]}[/cyan]")

    from repowise.core.ingestion import ChangeDetector

    detector = ChangeDetector(repo_path)
    file_diffs = detector.get_changed_files(base_ref, head or "HEAD")

    if not file_diffs:
        console.print("[green]No changed files detected.[/green]")
        save_state(repo_path, {**state, "last_sync_commit": head})
        return

    console.print(f"Changed files: [yellow]{len(file_diffs)}[/yellow]")

    # Show changed files
    for fd in file_diffs:
        status_color = {"added": "green", "deleted": "red", "modified": "yellow", "renamed": "blue"}
        color = status_color.get(fd.status, "white")
        console.print(f"  [{color}]{fd.status:>10}[/{color}]  {fd.path}")

    # Re-parse changed files and rebuild graph for affected pages
    from pathlib import Path as PathlibPath

    from repowise.core.generation import ContextAssembler, GenerationConfig, PageGenerator
    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    cfg = load_config(repo_path)
    language = cfg.get("language", "en")
    # Config-driven (saved by `repowise init`); CLI override not surfaced
    # on update yet — defaults to on to keep the onboarding collection
    # fresh as the codebase evolves.
    enable_onboarding_cfg = bool(cfg.get("enable_onboarding", True))
    config = GenerationConfig(
        max_concurrency=concurrency,
        language=language,
        reasoning=resolve_reasoning(reasoning, cfg),
        enable_onboarding=enable_onboarding_cfg,
    )

    # Read exclude patterns from config (set during init or via web UI)
    exclude_patterns: list[str] = list(cfg.get("exclude_patterns") or [])

    # Full re-ingest for graph (needed for cascade analysis)
    traverser = FileTraverser(repo_path, extra_exclude_patterns=exclude_patterns or None)
    file_infos = list(traverser.traverse())
    repo_structure = traverser.get_repo_structure()

    parser = ASTParser()
    parsed_files = []
    source_map: dict[str, bytes] = {}
    graph_builder = GraphBuilder(repo_path)

    for fi in file_infos:
        try:
            source = PathlibPath(fi.abs_path).read_bytes()
            parsed = parser.parse_file(fi, source)
            parsed_files.append(parsed)
            source_map[fi.path] = source
            graph_builder.add_file(parsed)
        except Exception:
            pass
    graph_builder.build()

    # Add framework-aware synthetic edges (conftest, Django, FastAPI, Flask)
    try:
        from repowise.core.generation.editor_files.tech_stack import detect_tech_stack

        tech_items = detect_tech_stack(repo_path)
        fw_count = graph_builder.add_framework_edges([item.name for item in tech_items])
        if fw_count:
            console.print(f"Framework edges added: [cyan]{fw_count}[/cyan]")
    except Exception:
        pass  # framework edge detection is best-effort

    # Re-index git metadata for changed files
    git_meta_map: dict[str, dict] = {}
    try:
        from repowise.core.ingestion.git_indexer import GitIndexer

        _commit_limit = cfg.get("commit_limit")
        _follow_renames = cfg.get("follow_renames", False)
        git_indexer = GitIndexer(
            repo_path,
            commit_limit=_commit_limit,
            follow_renames=_follow_renames,
        )
        changed_paths = [fd.path for fd in file_diffs]
        updated_meta = run_async(git_indexer.index_changed_files(changed_paths))
        git_meta_map = {m["file_path"]: m for m in updated_meta}
        graph_builder.update_co_change_edges(git_meta_map)
    except Exception as exc:
        console.print(f"[yellow]Git re-index skipped: {exc}[/yellow]")

    # Determine affected pages (auto-scale budget if not explicitly set)
    if cascade_budget is None:
        from repowise.core.ingestion.change_detector import compute_adaptive_budget

        cascade_budget = compute_adaptive_budget(file_diffs, len(file_infos))
        console.print(f"Adaptive cascade budget: [cyan]{cascade_budget}[/cyan]")
    affected = detector.get_affected_pages(file_diffs, graph_builder.graph(), cascade_budget)

    console.print(f"Pages to regenerate: [cyan]{len(affected.regenerate)}[/cyan]")
    if affected.decay_only:
        console.print(f"Pages to decay: [yellow]{len(affected.decay_only)}[/yellow]")

    if dry_run:
        console.print("[yellow]Dry run — no pages regenerated.[/yellow]")
        return

    # Run partial dead-code analysis up front so both branches can
    # persist its results. Previously this sat below the ``if index_only``
    # short-circuit, which left the closure's reference to
    # ``dead_code_report`` unbound and crashed every ``--index-only`` run.
    dead_code_report = None
    try:
        from repowise.core.analysis.dead_code import DeadCodeAnalyzer

        _analyzer_partial = DeadCodeAnalyzer(graph_builder.graph(), git_meta_map)
        _changed_paths_partial = [fd.path for fd in file_diffs]
        dead_code_report = _analyzer_partial.analyze_partial(_changed_paths_partial)
        if dead_code_report.total_findings:
            console.print(
                f"Dead code findings (partial): [yellow]{dead_code_report.total_findings}[/yellow]"
            )
    except Exception as exc:
        console.print(f"[yellow]Dead code analysis skipped: {exc}[/yellow]")

    if index_only:
        # Refresh graph, git metadata, and dead-code artifacts without
        # paying for LLM regeneration. Persist what we already computed
        # above and skip provider/decision/page-generation work.
        async def _persist_index_only() -> None:
            from repowise.cli.helpers import get_db_url_for_repo
            from repowise.core.persistence import (
                create_engine,
                create_session_factory,
                get_session,
                init_db,
                upsert_repository,
            )

            url = get_db_url_for_repo(repo_path)
            engine = create_engine(url)
            await init_db(engine)
            sf = create_session_factory(engine)

            async with get_session(sf) as session:
                repo = await upsert_repository(
                    session, name=repo_path.name, local_path=str(repo_path)
                )
                repo_id = repo.id

                if git_meta_map:
                    try:
                        from repowise.core.persistence.crud import (
                            recompute_git_percentiles,
                            upsert_git_metadata_bulk,
                        )

                        await upsert_git_metadata_bulk(
                            session, repo_id, list(git_meta_map.values())
                        )
                        await recompute_git_percentiles(session, repo_id)
                    except Exception as exc:
                        console.print(f"[yellow]Git persist skipped: {exc}[/yellow]")

                if dead_code_report is not None:
                    try:
                        from repowise.core.persistence.crud import (
                            save_dead_code_findings,
                        )

                        await save_dead_code_findings(
                            session, repo_id, dead_code_report.findings
                        )
                    except Exception as exc:
                        console.print(
                            f"[yellow]Dead-code persist skipped: {exc}[/yellow]"
                        )

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
                    console.print(
                        f"[yellow]Graph nodes persist skipped: {exc}[/yellow]"
                    )

        run_async(_persist_index_only())
        save_state(repo_path, {**state, "last_sync_commit": head})
        elapsed = time.monotonic() - start
        console.print(
            f"[green]Index-only update complete[/green] in {elapsed:.1f}s — "
            "graph + git + dead-code refreshed; LLM pages unchanged."
        )
        return

    provider = resolve_provider(provider_name, model, repo_path=repo_path)

    # Attach a DB-backed CostTracker so every LLM call made during this update
    # (decision rescan + page regeneration) is persisted to the `llm_costs`
    # table — matching what `repowise init` already does. Without this,
    # `repowise costs` only ever reflects the initial index and shows $0 for
    # all subsequent updates.
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.generation.cost_tracker import CostTracker
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )

    async def _make_cost_tracker() -> CostTracker:
        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
            return CostTracker(session_factory=sf, repo_id=repo.id)

    try:
        cost_tracker = run_async(_make_cost_tracker())
    except Exception:
        cost_tracker = CostTracker()
    provider._cost_tracker = cost_tracker

    # (dead_code_report computed above, before the index-only branch)

    # Re-scan changed files for inline decision markers
    new_decision_markers: list = []
    try:
        from repowise.core.analysis.decision_extractor import DecisionExtractor

        changed_paths = [fd.path for fd in file_diffs if fd.status in ("added", "modified")]
        if changed_paths:
            extractor = DecisionExtractor(
                repo_path=repo_path,
                provider=provider,
                graph=graph_builder.graph(),
                git_meta_map=git_meta_map,
            )
            new_decision_markers = run_async(
                extractor.scan_inline_markers(restrict_to_files=changed_paths)
            )
            if new_decision_markers:
                console.print(
                    f"New decision markers found: [green]{len(new_decision_markers)}[/green]"
                )
    except Exception as exc:
        console.print(f"[yellow]Decision re-scan skipped: {exc}[/yellow]")

    # Filter to only affected files
    regen_set = set(affected.regenerate)
    affected_parsed = [pf for pf in parsed_files if pf.file_info.path in regen_set]
    affected_source = {p: s for p, s in source_map.items() if p in regen_set}

    # Load prior pages so the generator can skip the LLM call for any affected
    # file whose freshly rendered prompt still hashes to the persisted value.
    async def _load_prior() -> dict[str, object]:
        from repowise.cli.helpers import get_db_url_for_repo
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            load_prior_pages,
            upsert_repository,
        )

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await upsert_repository(
                session, name=repo_path.name, local_path=str(repo_path)
            )
            return await load_prior_pages(session, repo.id)

    try:
        prior_pages = run_async(_load_prior())
    except Exception:
        prior_pages = {}

    # Generate affected pages
    assembler = ContextAssembler(config)
    generator = PageGenerator(
        provider, assembler, config, language=config.language, prior_pages=prior_pages
    )
    repo_name = repo_path.name

    generated_pages = run_async(
        generator.generate_all(
            affected_parsed,
            affected_source,
            graph_builder,
            repo_structure,
            repo_name,
            git_meta_map=git_meta_map,
        )
    )

    # Persist
    async def _persist() -> None:
        from repowise.cli.helpers import get_db_url_for_repo
        from repowise.core.persistence import (
            FullTextSearch,
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            upsert_page_from_generated,
            upsert_repository,
        )

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)

        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_name, local_path=str(repo_path))
            repo_id = repo.id
            for page in generated_pages:
                await upsert_page_from_generated(session, page, repo_id)

        # Persist updated git metadata + recompute percentiles
        if git_meta_map:
            try:
                from repowise.core.persistence.crud import (
                    recompute_git_percentiles,
                    upsert_git_metadata_bulk,
                )

                async with get_session(sf) as session:
                    await upsert_git_metadata_bulk(
                        session,
                        repo_id,
                        list(git_meta_map.values()),
                    )
                    await recompute_git_percentiles(session, repo_id)
            except Exception:
                pass  # git persistence is best-effort

        # Decision records: persist new markers + recompute staleness
        try:
            if new_decision_markers:
                import dataclasses as _dc

                from repowise.core.persistence.crud import bulk_upsert_decisions

                async with get_session(sf) as session:
                    await bulk_upsert_decisions(
                        session,
                        repo_id,
                        [_dc.asdict(d) for d in new_decision_markers],
                    )

            if git_meta_map:
                from repowise.core.persistence.crud import recompute_decision_staleness

                async with get_session(sf) as session:
                    await recompute_decision_staleness(session, repo_id, git_meta_map)
        except Exception:
            pass  # never fail update due to decision processing

        # Persist dead code findings (partial)
        if dead_code_report and dead_code_report.findings:
            try:
                import dataclasses as _dc_dead

                from repowise.core.persistence.crud import save_dead_code_findings

                async with get_session(sf) as session:
                    await save_dead_code_findings(
                        session,
                        repo_id,
                        [_dc_dead.asdict(f) for f in dead_code_report.findings],
                    )
            except Exception:
                pass  # dead code persistence is best-effort

        # Re-persist graph_nodes so symbol-level PageRank / betweenness
        # / community ids reflect the current build. Same rationale as
        # the index-only branch above — without this every per-symbol
        # metric stays at its original value forever.
        try:
            from repowise.core.pipeline.persist import persist_graph_nodes

            async with get_session(sf) as session:
                await persist_graph_nodes(session, repo_id, graph_builder)
        except Exception:
            pass  # graph node persistence is best-effort

        # Record a GenerationJob so the web UI "last synced" timestamp updates
        try:
            from datetime import UTC as _UTC
            from datetime import datetime

            from repowise.core.persistence.crud import upsert_generation_job

            async with get_session(sf) as session:
                now = datetime.now(_UTC)
                page_count = len(generated_pages)
                job = await upsert_generation_job(
                    session,
                    repository_id=repo_id,
                    status="completed",
                    total_pages=page_count,
                    config={"mode": "incremental", "source": "cli_update"},
                )
                job.completed_pages = page_count
                job.started_at = now
                job.finished_at = now
        except Exception:
            pass  # job recording is best-effort

        fts = FullTextSearch(engine)
        await fts.ensure_index()
        for page in generated_pages:
            await fts.index(page.page_id, page.title, page.content)

        await engine.dispose()

    run_async(_persist())

    # ---- Editor project files (best-effort) ----
    try:
        from repowise.cli.editor_setup import refresh_editor_project_files

        refresh_editor_project_files(console, repo_path)
    except Exception:
        pass  # Editor project-file refresh must never fail the update command

    # Update state
    state["last_sync_commit"] = head
    state["total_pages"] = state.get("total_pages", 0) + len(generated_pages)
    save_state(repo_path, state)

    # Trigger cross-repo hooks if this repo is part of a workspace
    try:
        ws_root = find_workspace_root(repo_path)
        if ws_root is not None:
            from repowise.core.workspace import WorkspaceConfig
            from repowise.core.workspace.update import run_cross_repo_hooks

            ws_config = WorkspaceConfig.load(ws_root)
            # Find this repo's alias in the workspace config
            from pathlib import Path as _P
            repo_abs = repo_path.resolve()
            alias = None
            for entry in ws_config.repos:
                if (ws_root / entry.path).resolve() == repo_abs:
                    alias = entry.alias
                    break
            if alias and len(ws_config.repos) >= 2:
                console.print("Running cross-repo analysis...")
                run_async(run_cross_repo_hooks(ws_config, ws_root, [alias]))
                console.print("[green]Cross-repo analysis updated.[/green]")
    except Exception:
        pass  # cross-repo hooks must never fail the update

    elapsed = time.monotonic() - start

    # Print generation report
    try:
        from repowise.core.generation.report import GenerationReport, render_report

        report = GenerationReport.from_pages(
            generated_pages,
            stale_count=len(affected.decay_only),
            decisions_count=len(new_decision_markers),
            elapsed=elapsed,
        )
        render_report(report, console)
    except Exception:
        # Fallback to simple message if report fails
        console.print(
            f"[bold green]Updated {len(generated_pages)} pages in {elapsed:.1f}s[/bold green]"
        )
