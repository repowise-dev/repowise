"""Multi-repo workspace initialization for ``repowise init``.

When ``init`` is pointed at a directory containing several git repos it branches
here: detect repos, prompt for selection + primary, write the workspace config,
then index (and optionally generate docs for) each repo. Generation routes
through the shared core in :mod:`.generation`; persistence + reporting reuse the
same helpers as the single-repo flow.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from repowise.cli._setup import setup_logging_silence
from repowise.cli.editor_integrations.defaults import (
    get_default_disabled_project_files,
    get_default_integration_overrides,
    get_default_project_file_overrides,
)
from repowise.cli.editor_setup import (
    register_editor_clients,
    resolve_editor_setup_options,
    write_editor_project_files,
)
from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    get_head_commit,
    load_config,
    load_state,
    resolve_provider,
    resolve_reasoning,
    run_async,
    save_config,
    save_config_partial,
    save_state,
)
from repowise.cli.providers import resolve_embedder
from repowise.cli.state_persistence import build_kg_state, save_knowledge_graph_json
from repowise.cli.ui import (
    BRAND,
    BRAND_STYLE,
    OWL_SPINNER,
    MaybeCountColumn,
    RichProgressCallback,
    interactive_advanced_config,
    interactive_mode_select,
    interactive_primary_select,
    interactive_provider_config_select,
    interactive_repo_select,
    load_dotenv,
    print_banner,
)
from repowise.core.generation.styles import DEFAULT_STYLE

from ._interactive import offer_distill_rewrite_hook, offer_hook_install
from .generation import (
    CostGateDeclined,
    cost_gate_declined,
    format_cost,
    run_repo_generation,
    select_coverage,
)
from .persistence import persist_result
from .reporting import show_workspace_completion


def _run_workspace_generation(
    *,
    repo_path: Path,
    result: Any,
    provider: Any,
    embedder_name_resolved: str,
    concurrency: int,
    yes: bool,
    resume: bool,
    skip_tests: bool,
    skip_infra: bool,
    test_run: bool,
    reasoning: str = "auto",
    onboarding: bool = True,
    coverage_pct: float | None = None,
    harvest_decisions: bool = True,
    wiki_style: str = DEFAULT_STYLE,
) -> list[Any]:
    """Run LLM generation for a single repo in the workspace init flow.

    Returns the list of generated pages. Raises :class:`CostGateDeclined` when
    the user declines the cost gate (caller persists the index without docs);
    other errors propagate so the caller can log per-repo failures without
    aborting the whole workspace run.
    """
    from repowise.core.generation import GenerationConfig

    gen_config = GenerationConfig(
        max_concurrency=concurrency,
        reasoning=resolve_reasoning(reasoning),
        enable_onboarding=onboarding,
        harvest_decisions=harvest_decisions,
        wiki_style=wiki_style,
    )
    chosen_pct, _plans, est, gen_config = select_coverage(
        result=result,
        gen_config=gen_config,
        provider=provider,
        repo_path=repo_path,
        skip_tests=skip_tests,
        skip_infra=skip_infra,
        coverage_pct=coverage_pct,
        yes=yes,
    )

    console.print(
        f"    Coverage: {int(chosen_pct * 100)}% / "
        f"~{est.estimated_input_tokens + est.estimated_output_tokens:,} tokens "
        f"({format_cost(est)}, {est.total_pages} pages)"
    )

    if cost_gate_declined(
        est, yes=yes, message=f"    Cost for {repo_path.name} exceeds $2.00. Continue?"
    ):
        console.print(
            "    [yellow]Skipped.[/yellow] "
            "[dim]Index will be saved without docs; "
            "future `repowise update` runs default to index-only.[/dim]"
        )
        # Sentinel — caller treats this exactly like an index-only run so
        # state.docs_enabled lands as False and the post-commit hook
        # doesn't surprise the user with LLM regen later.
        raise CostGateDeclined()

    return run_repo_generation(
        repo_path=repo_path,
        result=result,
        provider=provider,
        gen_config=gen_config,
        concurrency=concurrency,
        embedder_name_resolved=embedder_name_resolved,
        resume=resume,
        verbose=False,
    )


def _workspace_generation_provider_for_repo(provider: Any, repo_path: Path) -> Any:
    """Return a generation provider bound to the current workspace repo.

    The Codex CLI provider shells out ``codex exec --cd <repo>``, so it must be
    re-resolved against each repo's path; all other providers are path-agnostic
    and returned unchanged.
    """

    if getattr(provider, "provider_name", None) != "codex_cli":
        return provider
    return resolve_provider("codex_cli", getattr(provider, "model_name", None), repo_path)


@dataclass
class _WorkspaceCtx:
    """Run-wide settings shared across per-repo indexing in a workspace init."""

    provider: Any
    ws_config: Any
    editor_options: Any
    index_only: bool
    dry_run: bool
    force: bool
    follow_renames: bool
    include_submodules: bool
    exclude_patterns: list[str]
    skip_tests: bool
    skip_infra: bool
    concurrency: int
    test_run: bool
    yes: bool
    resume: bool
    onboarding: bool
    coverage_pct: float | None
    harvest_decisions: bool
    wiki_style: str
    resolved_reasoning: str
    embedder_name_resolved: str
    resolved_commit_limit: int
    run_mode: str = "standard"


@dataclass
class _RepoOutcome:
    """Result of indexing a single repo within a workspace."""

    error: str | None = None
    file_count: int = 0
    symbol_count: int = 0
    pages_generated: int = 0
    docs_outcome: tuple[int, str | None] = (0, None)


def _ingest_and_generate_repo(repo: Any, idx: int, total: int, ctx: _WorkspaceCtx) -> _RepoOutcome:
    """Index (and optionally generate docs for) one repo in a workspace init.

    Returns a :class:`_RepoOutcome`; on pipeline failure the outcome carries
    ``error`` and nothing is persisted (mirrors the per-repo try/except + continue
    the workspace loop used to do inline).
    """
    from repowise.core.pipeline import PhaseTimingRecorder, run_pipeline
    from repowise.core.pipeline.modes import OrchestratorMode

    console.print(
        f"  [{BRAND}][{idx}/{total}][/] Indexing [bold]{repo.alias}[/bold] ({repo.path.name})..."
    )
    ensure_repowise_dir(repo.path)

    try:
        with Progress(
            SpinnerColumn(spinner_name=OWL_SPINNER, style=BRAND_STYLE),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MaybeCountColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress_bar:
            callback = PhaseTimingRecorder(RichProgressCallback(progress_bar, console))

            _prev_state = load_state(repo.path)
            _prev_kg_fp = (
                _prev_state.get("knowledge_graph", {}).get("fingerprint") if not ctx.force else None
            )

            result = run_async(
                run_pipeline(
                    repo.path,
                    commit_depth=ctx.resolved_commit_limit,
                    follow_renames=ctx.follow_renames,
                    exclude_patterns=ctx.exclude_patterns if ctx.exclude_patterns else None,
                    include_submodules=ctx.include_submodules,
                    generate_docs=False,
                    mode=(
                        OrchestratorMode.FAST
                        if ctx.run_mode == "fast"
                        else OrchestratorMode.STANDARD
                    ),
                    progress=callback,
                    existing_kg_fingerprint=_prev_kg_fp,
                )
            )
        repo_phase_timings: dict[str, float] = callback.timings
        console.print(
            f"    [green]✓[/green] {result.file_count} files, {result.symbol_count:,} symbols"
        )
    except Exception as exc:
        console.print(f"    [red]✗ Failed: {exc}[/red]\n")
        return _RepoOutcome(error=str(exc))

    provider = ctx.provider
    index_only = ctx.index_only

    # Generation phase (per-repo, only when not index-only).
    # Track per-repo whether the user declined cost so state.docs_enabled
    # reflects the actual choice instead of the original init mode.
    repo_docs_enabled = not index_only and provider is not None
    skip_reason: str | None = None
    if index_only:
        skip_reason = "index-only mode"
    elif provider is None:
        skip_reason = "no provider configured"
    pages_generated = 0
    if not index_only and provider is not None:
        if ctx.dry_run:
            console.print("    [yellow]Dry run — skipping generation for this repo.[/yellow]\n")
            skip_reason = "dry run"
            repo_docs_enabled = False
        else:
            try:
                repo_provider = _workspace_generation_provider_for_repo(provider, repo.path)
                generated_pages = _run_workspace_generation(
                    repo_path=repo.path,
                    result=result,
                    provider=repo_provider,
                    embedder_name_resolved=ctx.embedder_name_resolved,
                    concurrency=ctx.concurrency,
                    yes=ctx.yes,
                    resume=ctx.resume,
                    skip_tests=ctx.skip_tests,
                    skip_infra=ctx.skip_infra,
                    test_run=ctx.test_run,
                    reasoning=ctx.resolved_reasoning,
                    onboarding=ctx.onboarding,
                    coverage_pct=ctx.coverage_pct,
                    harvest_decisions=ctx.harvest_decisions,
                    wiki_style=ctx.wiki_style,
                )
                result.generated_pages = generated_pages
                # (result.vector_store is set inside _run_workspace_generation
                # so the Phase-2C decision dedup can reuse the same store.)
                pages_generated = len(generated_pages)
                console.print(f"    [green]✓[/green] Generated {len(generated_pages)} pages\n")
            except CostGateDeclined:
                repo_docs_enabled = False
                result.generated_pages = []
                skip_reason = "cost gate declined"
            except Exception as gen_exc:
                console.print(f"    [yellow]Generation failed: {gen_exc}[/yellow]\n")
                skip_reason = f"generation error: {gen_exc}"
                repo_docs_enabled = False
    else:
        console.print()

    docs_outcome = (
        len(result.generated_pages or []),
        None if repo_docs_enabled else skip_reason,
    )

    # Persist to repo-local DB
    run_async(persist_result(result, repo.path))

    # Write state.json so `repowise update` knows the base commit
    head = get_head_commit(repo.path)
    pages_count = len(result.generated_pages or [])
    state: dict[str, Any] = {
        "last_sync_commit": head,
        "total_pages": pages_count,
        "docs_enabled": repo_docs_enabled,
    }
    if repo_docs_enabled and provider is not None:
        state["provider"] = provider.provider_name
        state["model"] = provider.model_name
    if repo_phase_timings:
        state["phase_timings"] = repo_phase_timings
    kg = getattr(result, "knowledge_graph_result", None)
    if kg is not None:
        state["knowledge_graph"] = build_kg_state(kg)
    save_state(repo.path, state)

    if kg is not None:
        save_knowledge_graph_json(repo.path, kg)

    # Update workspace config with indexing metadata
    entry = ctx.ws_config.get_repo(repo.alias)
    if entry is not None:
        entry.indexed_at = datetime.now(UTC).isoformat()
        entry.last_commit_at_index = head

    # MCP config + editor setup files per repo
    write_editor_project_files(
        console,
        repo.path,
        options=ctx.editor_options,
    )

    # Persist provider/model config per-repo when doing full generation
    if not index_only and provider is not None:
        from repowise.cli.providers.embedders import resolve_embedding_model

        save_config(
            repo.path,
            provider.provider_name,
            provider.model_name,
            ctx.embedder_name_resolved,
            embedding_model=resolve_embedding_model(ctx.embedder_name_resolved),
            exclude_patterns=ctx.exclude_patterns if ctx.exclude_patterns else None,
            commit_limit=ctx.resolved_commit_limit,
            reasoning=ctx.resolved_reasoning,
        )
        # Persist the wiki style per repo so update/restyle honor it. Default
        # omitted to keep config tidy — only an override is recorded.
        if ctx.wiki_style != DEFAULT_STYLE:
            save_config_partial(repo.path, wiki_style=ctx.wiki_style)

    return _RepoOutcome(
        file_count=result.file_count,
        symbol_count=result.symbol_count,
        pages_generated=pages_generated,
        docs_outcome=docs_outcome,
    )


def _run_cross_repo_analysis(ws_config: Any, root: Any, selected: list[Any], errors: list) -> None:
    """Run cross-repo analysis (co-changes, package deps, contracts) when ≥2 repos indexed."""
    indexed_aliases = [repo.alias for repo in selected if repo.alias not in [e[0] for e in errors]]
    if len(indexed_aliases) >= 2:
        console.print("  Running cross-repo analysis...")
        try:
            from repowise.core.workspace.update import run_cross_repo_hooks

            run_async(run_cross_repo_hooks(ws_config, root, indexed_aliases))
            console.print("  [green]✓[/green] Cross-repo analysis complete")
        except Exception as exc:
            console.print(f"  [yellow]⚠ Cross-repo analysis failed: {exc}[/yellow]")


def _workspace_init(
    *,
    scan: Any,
    init_all: bool,
    exclude_patterns: list[str],
    commit_limit: int | None,
    follow_renames: bool,
    no_claude_md: bool,
    agents_md: bool | None,
    codex_setup: bool | None,
    distill_hook: bool | None,
    include_submodules: bool,
    # Generation params (passed through from init_command)
    provider_name: str | None = None,
    model: str | None = None,
    embedder_name: str | None = None,
    index_only: bool = False,
    skip_tests: bool = False,
    skip_infra: bool = False,
    concurrency: int = 10,
    test_run: bool = False,
    reasoning: str | None = None,
    yes: bool = False,
    dry_run: bool = False,
    resume: bool = False,
    force: bool = False,
    onboarding: bool = True,
    coverage_pct: float | None = None,
    harvest_decisions: bool = True,
    wiki_style: str = DEFAULT_STYLE,
    run_mode: str = "standard",
) -> None:
    """Multi-repo workspace initialization.

    Detects repos, prompts for selection and primary, creates a workspace
    config, then runs ingestion on each repo.  When the user selects full or
    advanced mode (interactively) or passes an explicit provider, also runs
    LLM generation per repo.
    """
    setup_logging_silence()

    from repowise.core.workspace import RepoEntry, WorkspaceConfig

    start = time.monotonic()
    root = scan.root

    print_banner(console, repo_name=f"Workspace: {root.name}")
    console.print(f"  Detected [bold]{len(scan.repos)}[/bold] repositories in {root}\n")

    # Step 1: Select repos to index
    # --yes or --all: take every detected repo without prompting.
    select_all = init_all or yes
    selected = list(scan.repos) if select_all else interactive_repo_select(console, scan.repos)

    if not selected:
        console.print("[yellow]No repositories selected. Aborting.[/yellow]")
        return

    # Step 2: Select primary repo
    # --yes or --all: auto-pick the first repo as primary without prompting.
    primary_alias = (
        selected[0].alias if select_all else interactive_primary_select(console, selected)
    )

    # Determine root path (for provider resolution + dotenv)
    primary_repo = next((r for r in selected if r.alias == primary_alias), selected[0])
    load_dotenv(primary_repo.path)

    # Step 2b: Mode selection + provider setup
    # When running interactively with no explicit flags, present the mode menu.
    # --yes suppresses all interactive prompts: treat as non-interactive.
    is_interactive = sys.stdin.isatty() and provider_name is None and not index_only and not yes

    embedder_name_resolved = resolve_embedder(embedder_name)

    if is_interactive:
        mode = interactive_mode_select(console)
        if mode == "index_only":
            index_only = True
        elif mode == "advanced":
            selection = interactive_provider_config_select(
                console, model, reasoning, repo_path=primary_repo.path
            )
            provider_name = selection.provider_name
            model = selection.model
            reasoning = selection.reasoning
            # Pass the resolved style so the advanced generation section doesn't
            # add a per-workspace wiki-style prompt (the workspace flow applies
            # one style uniformly); onboarding / decision harvesting still apply.
            adv = interactive_advanced_config(
                console, prompt_reasoning=False, wiki_style=wiki_style
            )
            commit_limit = adv.get("commit_limit") or commit_limit
            follow_renames = adv.get("follow_renames", follow_renames)
            skip_tests = adv.get("skip_tests", skip_tests)
            skip_infra = adv.get("skip_infra", skip_infra)
            concurrency = adv.get("concurrency", concurrency)
            if adv.get("exclude"):
                exclude_patterns = list(exclude_patterns) + list(adv["exclude"])
            test_run = adv.get("test_run", test_run)
            reasoning = adv.get("reasoning") or reasoning
            embedder_name_resolved = resolve_embedder(adv.get("embedder") or embedder_name)
            onboarding = adv.get("onboarding", onboarding)
            harvest_decisions = adv.get("harvest_decisions", harvest_decisions)
            if adv.get("wiki_style"):
                wiki_style = adv["wiki_style"]
        elif not index_only:
            # "full" mode
            selection = interactive_provider_config_select(
                console, model, reasoning, repo_path=primary_repo.path
            )
            provider_name = selection.provider_name
            model = selection.model
            reasoning = selection.reasoning

    # Resolve provider once (shared across all repos for generation)
    primary_cfg = load_config(primary_repo.path)
    resolved_reasoning = resolve_reasoning(reasoning, primary_cfg)
    provider = None
    if not index_only:
        try:
            provider = resolve_provider(provider_name, model, primary_repo.path)
            # Re-resolve the embedder now that interactive provider selection
            # may have set the provider's API key in os.environ. Without
            # this, full-mode runs would display "mock" forever because
            # the initial resolution happened before the key was available.
            embedder_name_resolved = resolve_embedder(embedder_name)
            console.print(
                f"  Provider: [cyan]{provider.provider_name}[/cyan] / "
                f"Model: [cyan]{provider.model_name}[/cyan]"
            )
            console.print(f"  Embedder: [cyan]{embedder_name_resolved}[/cyan]\n")
            if resolved_reasoning != "auto":
                console.print(f"  Reasoning: [cyan]{resolved_reasoning}[/cyan]\n")
        except Exception as exc:
            console.print(
                f"  [yellow]Provider setup failed ({exc}); falling back to index-only.[/yellow]"
            )
            index_only = True
            provider = None

    # Step 3: Create workspace config
    entries = [
        RepoEntry(
            path=repo.path.relative_to(root).as_posix(),
            alias=repo.alias,
            is_primary=(repo.alias == primary_alias),
        )
        for repo in selected
    ]
    ws_config = WorkspaceConfig(
        version=1,
        repos=entries,
        default_repo=primary_alias,
    )
    config_path = ws_config.save(root)
    console.print(f"  [green]✓[/green] Created {config_path.name}")
    console.print()

    # Step 4: Index each selected repo (always generate_docs=False; generation is separate)
    resolved_commit_limit = max(1, min(commit_limit or 500, 10000))
    total_files = 0
    total_symbols = 0
    total_pages = 0
    errors: list[tuple[str, str]] = []
    # Per-repo docs outcome, surfaced in the completion panel so the user
    # never has to guess why the web UI is missing pages for some repos.
    # Maps alias -> (generated_count, skip_reason | None)
    docs_outcomes: dict[str, tuple[int, str | None]] = {}
    editor_options = resolve_editor_setup_options(
        console,
        disabled_project_files=get_default_disabled_project_files(
            no_claude_md=no_claude_md,
        ),
        project_file_overrides=get_default_project_file_overrides(
            agents_md=agents_md,
        ),
        integration_overrides=get_default_integration_overrides(
            codex_setup=codex_setup,
        ),
    )

    ctx = _WorkspaceCtx(
        provider=provider,
        ws_config=ws_config,
        editor_options=editor_options,
        index_only=index_only,
        dry_run=dry_run,
        force=force,
        follow_renames=follow_renames,
        include_submodules=include_submodules,
        exclude_patterns=exclude_patterns,
        skip_tests=skip_tests,
        skip_infra=skip_infra,
        concurrency=concurrency,
        test_run=test_run,
        yes=yes,
        resume=resume,
        onboarding=onboarding,
        coverage_pct=coverage_pct,
        harvest_decisions=harvest_decisions,
        wiki_style=wiki_style,
        resolved_reasoning=resolved_reasoning,
        embedder_name_resolved=embedder_name_resolved,
        resolved_commit_limit=resolved_commit_limit,
        run_mode=run_mode,
    )

    for i, repo in enumerate(selected, 1):
        outcome = _ingest_and_generate_repo(repo, i, len(selected), ctx)
        if outcome.error:
            errors.append((repo.alias, outcome.error))
            continue
        total_files += outcome.file_count
        total_symbols += outcome.symbol_count
        total_pages += outcome.pages_generated
        docs_outcomes[repo.alias] = outcome.docs_outcome

    # Save workspace config with updated timestamps
    ws_config.save(root)

    # Step 5: Cross-repo analysis (co-changes, package deps, contracts)
    _run_cross_repo_analysis(ws_config, root, selected, errors)

    # Step 6: Register primary repo with configured editor clients
    primary_entry = ws_config.get_primary()
    if primary_entry:
        primary_path = (root / primary_entry.path).resolve()
        register_editor_clients(console, primary_path)

    # Step 7: Completion summary
    elapsed = time.monotonic() - start
    show_workspace_completion(
        selected=selected,
        errors=errors,
        total_files=total_files,
        total_symbols=total_symbols,
        total_pages=total_pages,
        primary_alias=primary_alias,
        elapsed=elapsed,
        index_only=index_only,
        provider=provider,
        docs_outcomes=docs_outcomes,
    )

    # Offer to install post-commit hooks
    indexed_repos = [repo for repo in selected if repo.alias not in [e[0] for e in errors]]
    if indexed_repos:
        offer_hook_install(
            console,
            [r.path for r in indexed_repos],
            aliases=[r.alias for r in indexed_repos],
            yes=yes,
        )
    # Opt-in distill command-rewrite hook for Claude Code: one user-level
    # install, with the verdict recorded per repo. Applied to *all* selected
    # repos (not just successfully indexed ones, and even when every repo
    # failed) because ensure_repowise_dir already created `.repowise/` in
    # each, and the hook treats any repo with `.repowise/` and no recorded
    # verdict as enabled — a decline must gate every one of them off.
    offer_distill_rewrite_hook(console, [r.path for r in selected], distill_hook, yes=yes)
    console.print()
