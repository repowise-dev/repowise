"""``repowise update`` — incremental wiki regeneration for changed files.

This module holds the Click command + single-repo orchestration. The standalone
helpers it relies on live in sibling modules: mode resolution (:mod:`.mode`),
core delegators (:mod:`.incremental`), persistence + re-score
(:mod:`.persistence`), the workspace flow (:mod:`.workspace`), and report
rendering (:mod:`.reporting`).
"""

from __future__ import annotations

import time

import click
import structlog

from repowise.cli.helpers import (
    acquire_update_lock,
    clear_update_pending,
    clear_update_queued,
    console,
    ensure_repowise_dir,
    find_workspace_root,
    get_head_commit,
    load_config,
    load_state,
    read_update_lock,
    read_update_pending,
    release_update_lock,
    resolve_command_target,
    resolve_provider,
    resolve_reasoning,
    rotate_update_log_if_needed,
    run_async,
    save_state,
    write_update_pending,
)
from repowise.core.reasoning import REASONING_MODES

from .incremental import (
    _build_update_vector_store,
    _rebuild_graph_and_git,
    _run_partial_analysis,
)
from .mode import _infer_legacy_docs_enabled, _resolve_index_only_mode
from .persistence import (
    _persist_incremental_commits,
    _persist_index_only_update,
    _persist_partial_health,
    _run_full_health_rescore,
)
from .reporting import (
    _render_update_report,
    make_generation_progress,
    render_changed_files,
    render_header,
    show_full_completion,
)
from .workspace import _workspace_update

log = structlog.get_logger(__name__)


def _surface_release_news(*, written_by: str | None) -> None:
    """Show a post-upgrade "what's new" panel and a cached PyPI advisory.

    Best-effort and interactive-only (the caller gates on a terminal). The
    what's-new panel appears only when the store was written by an older repowise
    than the one running now.
    """
    from repowise.cli import __version__
    from repowise.cli.update_check import get_cli_update_check_cached
    from repowise.cli.whats_new import (
        load_changelog_entries,
        render_update_advisory,
        render_whats_new,
    )

    if written_by and written_by != __version__:
        entries = load_changelog_entries()
        render_whats_new(
            console,
            entries,
            since_version=written_by,
            up_to_version=__version__,
            title=f"Upgraded to v{__version__} - what's new",
        )
    render_update_advisory(console, get_cli_update_check_cached())


@click.command("update")
@click.argument("path", required=False, default=None)
@click.option("--provider", "provider_name", default=None, help="LLM provider name.")
@click.option("--model", default=None, help="Model identifier override.")
@click.option("--since", default=None, help="Base git ref to diff from (overrides state).")
@click.option("--concurrency", type=int, default=10, help="Max concurrent LLM calls.")
@click.option(
    "--reasoning",
    type=click.Choice(REASONING_MODES),
    default=None,
    help=(
        "Reasoning mode for supported providers: auto, off/none, minimal, "
        "low, medium, high, xhigh, or max."
    ),
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
@click.option(
    "--full",
    "full",
    is_flag=True,
    default=False,
    help=(
        "Upgrade a fast (`--mode fast`) index to a full one: backfill the git "
        "tier ESSENTIAL -> FULL and generate the LLM docs that fast mode "
        "skipped. Incremental — reuses the persisted graph instead of "
        "re-parsing and re-resolving it. Single-repo only."
    ),
)
@click.option(
    "--no-cost-tracking",
    is_flag=True,
    default=False,
    help=(
        "Skip DB-backed LLM cost tracking for this run. Avoids opening a second "
        "engine on wiki.db, so cost writes can never contend with the "
        "generation writer. The live cost readout still works; only historical "
        "`repowise costs` rows are skipped. Also settable via the "
        "REPOWISE_NO_COST_TRACKING env var."
    ),
)
@click.option(
    "--agents/--no-agents",
    "agents_md",
    default=None,
    help="Generate managed AGENTS.md after update (default: config or enabled).",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help=(
        "Show the full changed-file list and per-phase internals (adaptive "
        "cascade budget, decision-marker/evolution counts, best-effort skip "
        "warnings, and the detailed generation report)."
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
    full: bool = False,
    agents_md: bool | None = None,
    concurrency: int = 10,
    no_cost_tracking: bool = False,
    verbose: bool = False,
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
        if full:
            raise click.ClickException(
                "--full is single-repo only. Run it inside a specific repo "
                "(or pass --no-workspace / --repo <alias>)."
            )
        _workspace_update(target, dry_run=dry_run, agents_md=agents_md, verbose=verbose)
        return

    # --- Single-repo path from here on. ---
    repo_path = target.repo_path
    assert repo_path is not None  # single mode always sets repo_path
    ensure_repowise_dir(repo_path)

    # If this repo is a workspace member updated here for the first time,
    # inherit the workspace's distill rewrite-hook verdict (best-effort,
    # no-op outside a workspace or once a verdict exists).
    from repowise.cli.commands.workspace_cmd import inherit_workspace_distill_verdict

    inherit_workspace_distill_verdict(repo_path)

    # --- Fast -> full upgrade (--full): a distinct path that reuses the
    # persisted graph rather than diffing changed files. Dispatched before any
    # incremental change-detection so the normal `repowise update` flow below
    # is byte-for-byte unchanged. ---
    if full:
        from repowise.cli.commands.upgrade_flow import upgrade_to_full

        upgrade_to_full(
            repo_path,
            provider_name=provider_name,
            model=model,
            reasoning=reasoning,
            concurrency=concurrency,
        )
        return
    # Truncate the hook-managed log if it has grown past the cap. The hook
    # appends each run unconditionally — without this opportunistic rotation
    # at the start of every CLI update, a busy repo's ``.update.log`` would
    # balloon to MBs over time.
    rotate_update_log_if_needed(repo_path)

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
            "Run 'repowise init' there first, or pass --since <ref>." + hint
        )

    # A config.yaml / health-rules.json change warrants a full health re-score
    # even when git is unchanged. A missing prior fingerprint is backfilled, not
    # treated as a change. ``config_changed`` gates every "nothing to do" path.
    from repowise.cli.helpers import config_fingerprint

    prev_config_fp = state.get("config_fingerprint")
    curr_config_fp = config_fingerprint(repo_path)
    config_changed = prev_config_fp is not None and prev_config_fp != curr_config_fp

    if head and head == base_ref and not config_changed:
        console.print("[green]Already up to date.[/green]")
        if prev_config_fp is None and not dry_run:
            save_state(repo_path, {**state, "config_fingerprint": curr_config_fp})
        return

    # --- Single-flight check ------------------------------------------------
    # A fresh lock from another process means a `repowise update` is already
    # running on this repo. Two updates racing on save_state was the actual
    # root cause of "wiki keeps going stale": post-commit hooks fired during
    # rapid-fire commits would each redo full ingestion + generation from the
    # same outdated base, take 10+ minutes, then save_state out of order so
    # state.json never reflected reality. Bail cleanly instead — and leave
    # the new HEAD in ``.update.pending`` so the running update can roll
    # forward to it at the end of its current pass.
    existing_lock = read_update_lock(repo_path)
    if existing_lock is not None:
        import time as _time

        elapsed = int(_time.time() - existing_lock.get("started_at", _time.time()))
        target_short = (existing_lock.get("target_commit") or "")[:8]
        write_update_pending(repo_path, head)
        console.print(
            f"[yellow]Another `repowise update` is already running "
            f"(pid {existing_lock.get('pid')}, target {target_short}, "
            f"started {elapsed}s ago).[/yellow]"
        )
        console.print(
            f"[dim]HEAD {head[:8] if head else 'HEAD'} marked as pending; "
            "the running update will roll forward to it.[/dim]"
        )
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
    index_only = _resolve_index_only_mode(index_only=index_only, docs_flag=docs_flag, state=state)

    # --- Acquire update lock so the augment hook can suppress its
    # stale-wiki warning while this run is in flight (typical case: the
    # post-commit hook fires `repowise update` in the background, then a
    # follow-on tool call would otherwise warn that HEAD has moved). ---
    import atexit

    acquire_update_lock(repo_path, head)
    # Drop the queued marker now that the real lock owns the suppression
    # window. Leaving both behind would cause the augment hook to keep
    # suppressing for the queued-stale-after duration even past a failed run.
    clear_update_queued(repo_path)
    atexit.register(release_update_lock, repo_path)
    atexit.register(clear_update_queued, repo_path)

    # --- Store-format upgrade assessment --------------------------------
    # Single decision point for "does upgrading repowise need to touch this
    # store". Runs any no-LLM auto actions (e.g. re-embed after an embedding
    # model change); reindex is only ever recommended, never forced here.
    # Placed after the single-flight lock so an auto re-embed (a full vector
    # rebuild) can't race a concurrent update. Best-effort: the upgrade layer
    # must never block a routine update.
    try:
        from repowise.cli.upgrade import apply_upgrade, assess_store

        verdict = assess_store(repo_path)
        if not verdict.is_noop:
            if verdict.reindex_recommended and verdict.reindex_command:
                console.print(f"[yellow]Reindex recommended:[/yellow] {verdict.reindex_command}")
                if verdict.user_notice:
                    console.print(f"[dim]{verdict.user_notice}[/dim]")
            if verdict.actions and not dry_run:
                for action in verdict.actions:
                    console.print(f"[dim]Upgrade: {action.reason}[/dim]")
                run_async(apply_upgrade(repo_path, verdict))
        # Surface "what's new" + a PyPI advisory only in an interactive terminal,
        # so the background post-commit hook never spams output. The store's
        # ``written_by`` (read before this run re-stamps it) bounds the panel to
        # releases the user has actually crossed.
        if console.is_terminal:
            _surface_release_news(written_by=verdict.written_by)
    except Exception as exc:  # never block a routine update on the upgrade layer
        log.debug("store_upgrade_skipped", error=str(exc))

    render_header(repo_path, base_ref, head)

    from repowise.core.ingestion import ChangeDetector

    detector = ChangeDetector(repo_path)
    file_diffs = detector.get_changed_files(base_ref, head or "HEAD")

    if not file_diffs and not config_changed:
        console.print("[green]No changed files detected.[/green]")
        save_state(
            repo_path,
            {**state, "last_sync_commit": head, "config_fingerprint": curr_config_fp},
        )
        return

    if config_changed:
        # Full re-score (not the partial update) so unchanged files pick up the
        # new rules/excludes instead of being left stale.
        console.print("[yellow]Config files changed — re-running health analysis.[/yellow]")
        if dry_run:
            console.print("[yellow]Dry run — health would be re-scored. No changes made.[/yellow]")
            return
        cfg = load_config(repo_path)
        exclude_patterns = list(cfg.get("exclude_patterns") or [])
        _run_full_health_rescore(repo_path, exclude_patterns, state, head, curr_config_fp)
        return

    render_changed_files(file_diffs, verbose=verbose)

    # Re-parse changed files and rebuild graph for affected pages
    cfg = load_config(repo_path)

    # Read exclude patterns from config (set during init or via web UI)
    exclude_patterns: list[str] = list(cfg.get("exclude_patterns") or [])

    parsed_files, source_map, graph_builder, repo_structure, file_count, git_meta_map = (
        _rebuild_graph_and_git(
            repo_path,
            file_diffs,
            cfg,
            exclude_patterns,
            git_tier=state.get("git_tier"),
            include_submodules=bool(state.get("include_submodules", False)),
            include_nested_repos=bool(state.get("include_nested_repos", False)),
        )
    )

    # Determine affected pages (auto-scale budget if not explicitly set)
    if cascade_budget is None:
        from repowise.core.ingestion.change_detector import compute_adaptive_budget

        cascade_budget = compute_adaptive_budget(file_diffs, file_count)
        if verbose:
            console.print(f"Adaptive cascade budget: [cyan]{cascade_budget}[/cyan]")
    affected = detector.get_affected_pages(file_diffs, graph_builder.graph(), cascade_budget)

    console.print(f"Pages to regenerate: [cyan]{len(affected.regenerate)}[/cyan]")
    if affected.decay_only:
        console.print(f"Pages to decay: [yellow]{len(affected.decay_only)}[/yellow]")

    if dry_run:
        console.print("[yellow]Dry run — no pages regenerated.[/yellow]")
        return

    partial_health_report, dead_code_report = _run_partial_analysis(
        repo_path, graph_builder, git_meta_map, parsed_files, file_diffs
    )

    # Partial health has consumed the per-file ``BlameIndex``; drop it before
    # the metadata reaches persistence / regeneration so the transient,
    # non-serializable object can never leak downstream (mirrors run_pipeline).
    from repowise.core.pipeline.phases.git import drop_transient_git_signals

    drop_transient_git_signals(list(git_meta_map.values()))

    if index_only:
        _persist_index_only_update(
            repo_path,
            graph_builder,
            git_meta_map,
            dead_code_report,
            partial_health_report,
            state,
            head,
            start,
            [fd.path for fd in file_diffs],
            file_diffs=file_diffs,
        )
        return

    # The generation/LLM layer is only needed past this point — importing it
    # above the index-only branch would make every index-only update (the
    # post-commit hook's hot path) pay the import for code it never runs.
    from repowise.core.generation import ContextAssembler, GenerationConfig, PageGenerator

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
        # Honor the wiki style chosen at init (or via `repowise restyle`) so pages
        # regenerated for changed files match the rest of the wiki's voice.
        wiki_style=cfg.get("wiki_style", "comprehensive"),
    )

    provider = resolve_provider(provider_name, model, repo_path=repo_path)

    # Attach a DB-backed CostTracker so every LLM call made during this update
    # (decision rescan + page regeneration) is persisted to the `llm_costs`
    # table — matching what `repowise init` already does. Without this,
    # `repowise costs` only ever reflects the initial index and shows $0 for
    # all subsequent updates.
    from repowise.cli.providers import build_cost_tracker

    cost_tracker = build_cost_tracker(repo_path, repo_path.name, no_cost_tracking=no_cost_tracking)
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
            if new_decision_markers and verbose:
                console.print(
                    f"New decision markers found: [green]{len(new_decision_markers)}[/green]"
                )
    except Exception as exc:
        if verbose:
            console.print(f"[yellow]Decision re-scan skipped: {exc}[/yellow]")

    # Count of decision records touched by evolution, surfaced in the panel.
    decisions_evolved = 0

    # Build the shared vector store once: reused by the supersession detector
    # below and by the decision upsert in _persist (semantic dedup + search).
    decision_vector_store = _build_update_vector_store(repo_path, cfg)

    # --- Two-pass decision evolution (Phase 3C), BEFORE page regeneration ----
    # Cross-reference the diff + new commit bodies against existing decisions
    # governing the changed files (Pass 1, cheap) then ask the LLM whether each
    # survivor is amended/superseded/reaffirmed (Pass 2, gated). Governed pages
    # of an evolved decision are folded into ``affected.regenerate`` so they
    # re-render in this same run — the cascade-coupling the design calls for.
    try:
        import json as _json_evo

        evidence_by_file: dict[str, str] = {}
        for fd in file_diffs:
            if fd.status not in ("added", "modified"):
                continue
            parts: list[str] = []
            if fd.trigger_commit_message:
                parts.append(fd.trigger_commit_message)
            if fd.diff_text:
                parts.append(fd.diff_text)
            meta = git_meta_map.get(fd.path)
            if meta:
                for c in _json_evo.loads(meta.get("significant_commits_json", "[]") or "[]"):
                    msg = c.get("message") or ""
                    body = c.get("body") or ""
                    if msg or body:
                        parts.append(f"{msg}\n{body}")
            if parts:
                evidence_by_file[fd.path] = "\n\n".join(parts)

        changed_set = {fd.path for fd in file_diffs if fd.status in ("added", "modified")}
        if changed_set:
            from repowise.core.analysis.decision_evolution import run_update_evolution

            async def _run_evolution() -> dict:
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
                    res = await run_update_evolution(
                        session,
                        repo.id,
                        changed_files=changed_set,
                        evidence_by_file=evidence_by_file,
                        provider=provider,
                    )
                await engine.dispose()
                return res

            evo = run_async(_run_evolution())
            decisions_evolved = (
                evo.get("superseded", 0) + evo.get("amended", 0) + evo.get("reaffirmed", 0)
            )
            evo_regen = evo.get("regen_files") or set()
            if evo_regen:
                # page_id == file path for file pages, so adding the governed
                # file paths schedules their pages for regeneration.
                affected.regenerate = list(
                    dict.fromkeys([*affected.regenerate, *sorted(evo_regen)])
                )
                if verbose:
                    console.print(
                        f"Decision evolution: [cyan]{evo.get('superseded', 0)} superseded[/cyan], "
                        f"[cyan]{evo.get('amended', 0)} amended[/cyan], "
                        f"[green]{evo.get('reaffirmed', 0)} reaffirmed[/green]; "
                        f"+{len(evo_regen)} governed page(s) queued for regen."
                    )
    except Exception as exc:
        if verbose:
            console.print(f"[yellow]Decision evolution skipped: {exc}[/yellow]")

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
            repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
            return await load_prior_pages(session, repo.id)

    try:
        prior_pages = run_async(_load_prior())
    except Exception:
        prior_pages = {}

    # Generate affected pages. The vector store (shared with the decision
    # pass above) must ride along: without it regenerated pages are never
    # re-embedded, so every update silently evicted its pages from semantic
    # search — on long-lived repos the LanceDB corpus ended up holding
    # decisions and structural pages but zero current file pages.
    assembler = ContextAssembler(config)
    generator = PageGenerator(
        provider,
        assembler,
        config,
        vector_store=decision_vector_store,
        language=config.language,
        prior_pages=prior_pages,
        repo_path=repo_path,
    )
    repo_name = repo_path.name

    # Drive a live progress bar (owl spinner + running cost) off generate_all's
    # page callbacks, matching init's generation phase. Cost is read from the
    # tracker as each page lands so the readout ticks up in real time.
    with make_generation_progress() as gen_progress:
        gen_task = gen_progress.add_task("Generating pages...", total=None, cost=0.0)

        def _on_total_known(total: int) -> None:
            gen_progress.update(gen_task, total=total)

        def _on_page_done(_page_id: str) -> None:
            gen_progress.update(gen_task, advance=1, cost=cost_tracker.session_cost)

        generated_pages = run_async(
            generator.generate_all(
                affected_parsed,
                affected_source,
                graph_builder,
                repo_structure,
                repo_name,
                on_page_done=_on_page_done,
                on_total_known=_on_total_known,
                git_meta_map=git_meta_map,
                repo_path=repo_path,
            )
        )

    # Flush the buffered LLM cost rows now that generation is done — a single
    # transaction outside the contended generation window (issue #326).
    from repowise.cli.providers import flush_cost_tracker

    flush_cost_tracker(cost_tracker)

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
            # Tombstone pages for deleted/renamed files — regeneration only
            # rewrites pages for files that still exist.
            try:
                from repowise.core.pipeline.persist import (
                    mark_tombstone_pages,
                    tombstone_candidates,
                )

                await mark_tombstone_pages(session, repo_id, tombstone_candidates(file_diffs))
            except Exception as exc:
                if verbose:
                    console.print(f"[yellow]Tombstone marking skipped: {exc}[/yellow]")

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
                    await _persist_incremental_commits(session, repo_id, repo_path)
            except Exception:
                pass  # git persistence is best-effort

        # Decision records: persist new markers + harvested decisions, detect
        # supersession, recompute staleness.
        try:
            decision_dicts: list[dict] = []
            if new_decision_markers:
                import dataclasses as _dc

                decision_dicts.extend(_dc.asdict(d) for d in new_decision_markers)
            # Phase-2 follow-up: also harvest decisions emitted by the page
            # generator during this update (each gated at generation time).
            for page in generated_pages:
                harvested = page.metadata.get("harvested_decisions")
                if harvested:
                    decision_dicts.extend(harvested)

            if decision_dicts:
                from repowise.core.persistence.crud import bulk_upsert_decisions

                async with get_session(sf) as session:
                    touched_ids = await bulk_upsert_decisions(
                        session,
                        repo_id,
                        decision_dicts,
                        vector_store=decision_vector_store,
                    )
                    # Phase 3B: supersede/conflict detection over the touched
                    # records (gated LLM judge available on this path).
                    if touched_ids and decision_vector_store is not None:
                        from repowise.core.analysis.decision_evolution import (
                            detect_supersessions_and_conflicts,
                        )

                        await detect_supersessions_and_conflicts(
                            session,
                            repo_id,
                            touched_ids=touched_ids,
                            vector_store=decision_vector_store,
                            provider=provider,
                        )

            if git_meta_map:
                from repowise.core.persistence.crud import recompute_decision_staleness

                async with get_session(sf) as session:
                    await recompute_decision_staleness(session, repo_id, git_meta_map)

            # Governance findings pass: runs after decisions + staleness are
            # up to date. Best-effort — never breaks the update.
            try:
                from sqlalchemy import select as _sel_dec

                from repowise.core.analysis.health.governance import build_governance_findings
                from repowise.core.persistence.crud import (
                    get_decision_health_summary,
                    replace_governance_findings,
                )
                from repowise.core.persistence.models import DecisionRecord

                async with get_session(sf) as session:
                    _dr = await session.execute(
                        _sel_dec(DecisionRecord).where(DecisionRecord.repository_id == repo_id)
                    )
                    _decisions = list(_dr.scalars().all())
                    _summary = await get_decision_health_summary(session, repo_id)
                    _gov = build_governance_findings(
                        health_summary=_summary,
                        decisions=_decisions,
                    )
                    await replace_governance_findings(session, repo_id, _gov)
            except Exception:
                pass  # governance findings are best-effort
        except Exception:
            pass  # never fail update due to decision processing

        # Persist code-health findings + metrics (partial — upsert only)
        if partial_health_report is not None:
            try:
                async with get_session(sf) as session:
                    await _persist_partial_health(session, repo_id, partial_health_report)
            except Exception:
                pass  # health persistence is best-effort

        # Scoped to changed files so unchanged files keep their findings (#295).
        if dead_code_report is not None:
            try:
                import dataclasses as _dc_dead

                from repowise.core.persistence.crud import upsert_dead_code_findings

                async with get_session(sf) as session:
                    await upsert_dead_code_findings(
                        session,
                        repo_id,
                        [_dc_dead.asdict(f) for f in dead_code_report.findings],
                        file_paths=[fd.path for fd in file_diffs],
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
        from repowise.cli.editor_integrations.defaults import get_default_project_file_overrides
        from repowise.cli.editor_setup import EditorSetupOptions, refresh_editor_project_files

        editor_options = None
        if agents_md is not None:
            editor_options = EditorSetupOptions(
                project_file_overrides=get_default_project_file_overrides(
                    agents_md=agents_md,
                ),
            )
        refresh_editor_project_files(
            console,
            repo_path,
            options=editor_options,
        )
    except Exception:
        pass  # Editor project-file refresh must never fail the update command

    # Update state
    from repowise.cli.helpers import config_fingerprint

    state["last_sync_commit"] = head
    state["total_pages"] = state.get("total_pages", 0) + len(generated_pages)
    state["config_fingerprint"] = config_fingerprint(repo_path)
    save_state(repo_path, state)

    # --- Roll forward to any commit that landed during this run ------------
    # Another `repowise update` (from a post-commit hook) may have written a
    # ``.update.pending`` marker while we were generating pages. If the new
    # HEAD points past where we just finished, clear the marker only if it's
    # already caught up; otherwise leave it in place as a signal to the
    # augment hook so its message can be "update done, new commits since"
    # instead of the bare stale-wiki warning.
    pending_head = read_update_pending(repo_path)
    if pending_head and pending_head == head:
        clear_update_pending(repo_path)

    # Trigger cross-repo hooks if this repo is part of a workspace
    try:
        ws_root = find_workspace_root(repo_path)
        if ws_root is not None:
            from repowise.core.workspace import WorkspaceConfig
            from repowise.core.workspace.update import run_cross_repo_hooks

            ws_config = WorkspaceConfig.load(ws_root)
            # Find this repo's alias in the workspace config

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
    show_full_completion(
        generated_pages=generated_pages,
        decay_count=len(affected.decay_only),
        decisions_changed=len(new_decision_markers) + decisions_evolved,
        provider=provider,
        cost=cost_tracker.session_cost,
        tokens=cost_tracker.session_tokens,
        elapsed=elapsed,
    )
    if verbose:
        _render_update_report(generated_pages, affected, new_decision_markers, elapsed)
