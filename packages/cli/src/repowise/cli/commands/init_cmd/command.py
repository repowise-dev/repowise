"""``repowise init`` — full wiki generation for a repository.

This module owns the Click command and the single-repo orchestration. The
multi-repo path lives in :mod:`.workspace`; generation, persistence and console
rendering live in :mod:`.generation`, :mod:`.persistence` and :mod:`.reporting`
respectively, and are shared by both flows.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import click
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

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
    config_fingerprint,
    console,
    ensure_repowise_dir,
    get_head_commit,
    load_config,
    load_state,
    resolve_provider,
    resolve_reasoning,
    resolve_repo_path,
    run_async,
    save_config_partial,
    save_state,
)
from repowise.cli.providers import resolve_embedder
from repowise.cli.state_persistence import build_kg_state, save_knowledge_graph_json
from repowise.cli.ui import (
    BRAND,
    MaybeCountColumn,
    RichProgressCallback,
    interactive_advanced_config,
    interactive_fast_mode_offer,
    interactive_mode_select,
    interactive_provider_config_select,
    load_dotenv,
    print_banner,
    print_index_only_intro,
    print_phase_header,
    print_scan_summary,
    quick_repo_scan,
    should_offer_fast_mode,
)
from repowise.core.reasoning import REASONING_MODES

from ._interactive import offer_hook_install
from .generation import cost_gate_declined, format_cost, run_repo_generation, select_coverage
from .persistence import (
    build_resume_controller,
    effective_run_mode_for_resume,
    git_tier_for_run_mode,
    persist_result,
    save_full_state_and_config,
)
from .reporting import show_analysis_summary, show_completion
from .workspace import _workspace_init


def _run_generation_phase(
    *,
    repo_path: Path,
    result: Any,
    provider: Any,
    total_phases: int,
    concurrency: int,
    language: str,
    resolved_reasoning: str,
    onboarding: bool,
    tier1_top_n: int | None,
    harvest_decisions: bool,
    coverage_pct: float | None,
    yes: bool,
    dry_run: bool,
    skip_tests: bool,
    skip_infra: bool,
    embedder_name_resolved: str,
    resume: bool,
) -> tuple[bool, bool]:
    """Run the LLM generation phase for a single-repo init.

    Returns ``(stop, cost_declined)``: ``stop`` is True when this was a dry run
    and the caller should return immediately; ``cost_declined`` is True when the
    user declined the cost gate (generation skipped, index still saved). Mutates
    ``result`` in place with the generated pages, vector store, and enriched KG.
    """
    from repowise.core.generation import GenerationConfig

    print_phase_header(
        console,
        3,
        total_phases,
        "Generation",
        f"Generating wiki pages with {provider.provider_name} / {provider.model_name}",
    )

    gen_config = GenerationConfig(
        max_concurrency=concurrency,
        language=language,
        reasoning=resolved_reasoning,
        enable_onboarding=onboarding,
        tier1_top_n=tier1_top_n,
        harvest_decisions=harvest_decisions,
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

    table = Table(title="Generation Plan", border_style=BRAND)
    table.add_column("Page Type", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Level", justify="right")
    for plan in est.plans:
        table.add_row(plan.page_type, str(plan.count), str(plan.level))
    table.add_section()
    table.add_row("[bold]Total[/bold]", f"[bold]{est.total_pages}[/bold]", "")
    console.print(table)

    # Language breakdown
    lang_dist = result.repo_structure.root_language_distribution
    if lang_dist:
        lang_items = sorted(lang_dist.items(), key=lambda x: -x[1])[:6]
        lang_parts = [f"{lang} {pct:.0%}" for lang, pct in lang_items]
        console.print(f"  Languages: {', '.join(lang_parts)}")

    console.print(
        f"  Coverage: {int(chosen_pct * 100)}% / "
        f"~{est.estimated_input_tokens + est.estimated_output_tokens:,} tokens "
        f"({format_cost(est)})"
    )
    if onboarding:
        console.print(
            "  [cyan]Onboarding collection:[/cyan] "
            "[dim]up to 8 curated pages — Project Overview, Architecture Guide, "
            "Getting Started, Codebase Map, Key Concepts, How It Works, "
            "Development Guide, Active Landscape "
            "(slots without enough signal are skipped).[/dim]"
        )
    else:
        console.print("  [dim]Onboarding collection: disabled (--no-onboarding).[/dim]")
    console.print()

    if dry_run:
        console.print("[yellow]Dry run — no pages generated.[/yellow]")
        return True, False

    if cost_gate_declined(est, yes=yes, message="  Estimated cost exceeds $2.00. Continue?"):
        console.print(
            "[yellow]Skipped LLM generation.[/yellow] "
            "[dim]Index/graph/git/dead-code will be saved; future "
            "`repowise update` runs default to index-only so the "
            "post-commit hook won't trigger LLM regen.[/dim]"
        )
        return False, True

    run_repo_generation(
        repo_path=repo_path,
        result=result,
        provider=provider,
        gen_config=gen_config,
        concurrency=concurrency,
        embedder_name_resolved=embedder_name_resolved,
        resume=resume,
        verbose=True,
    )
    return False, False


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("init")
@click.argument("path", required=False, default=None)
@click.option(
    "--provider",
    "provider_name",
    default=None,
    help=(
        "LLM provider name (anthropic, openai, openrouter, gemini, "
        "deepseek, ollama, litellm, codex_cli, mock)."
    ),
)
@click.option("--model", default=None, help="Model identifier override.")
@click.option(
    "--embedder",
    "embedder_name",
    default=None,
    type=click.Choice(["gemini", "openai", "openrouter", "ollama", "mock"]),
    help="Embedder for RAG: gemini | openai | openrouter | ollama | mock (default: auto-detect).",
)
@click.option("--skip-tests", is_flag=True, default=False, help="Skip test files.")
@click.option("--skip-infra", is_flag=True, default=False, help="Skip infrastructure files.")
@click.option(
    "--dry-run", is_flag=True, default=False, help="Show generation plan without running."
)
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip cost confirmation prompt.")
@click.option("--resume", is_flag=True, default=False, help="Resume from last checkpoint.")
@click.option(
    "--force", is_flag=True, default=False, help="Regenerate all pages, ignoring existing."
)
@click.option("--concurrency", type=int, default=5, help="Max concurrent LLM calls.")
@click.option(
    "--reasoning",
    type=click.Choice(REASONING_MODES),
    default=None,
    help=(
        "Reasoning mode for supported providers: auto, off/none, minimal, "
        "low, medium, high, xhigh, or max. Default: auto."
    ),
)
@click.option(
    "--test-run",
    is_flag=True,
    default=False,
    help="Limit generation to top 10 files by PageRank for quick validation.",
)
@click.option(
    "--index-only",
    is_flag=True,
    default=False,
    help="Index files, git history, graph, and dead code — skip LLM page generation.",
)
@click.option(
    "--mode",
    "run_mode",
    type=click.Choice(["standard", "fast"]),
    default="standard",
    help=(
        "Pipeline depth. 'fast' indexes graph + essential git only (no per-file "
        "blame, no co-change, no LLM docs) for a quick first pass on very large "
        "repos; backfill the rest later. Default: standard."
    ),
)
@click.option(
    "--exclude",
    "-x",
    multiple=True,
    metavar="PATTERN",
    help="Gitignore-style pattern to exclude. Can be repeated: -x vendor/ -x 'src/generated/**'",
)
@click.option(
    "--commit-limit",
    type=int,
    default=None,
    help="Max commits to analyze per file and for co-change detection (default: 500, max: 5000). Saved to config.",
)
@click.option(
    "--follow-renames",
    is_flag=True,
    default=False,
    help="Use git log --follow to track files across renames (slower but more accurate history). Saved to config.",
)
@click.option(
    "--no-claude-md",
    "no_claude_md",
    is_flag=True,
    default=False,
    help="Skip generating CLAUDE.md. Saves 'editor_files.claude_md: false' to config.",
)
@click.option(
    "--agents/--no-agents",
    "agents_md",
    default=None,
    help="Generate managed AGENTS.md (default: config or enabled).",
)
@click.option(
    "--codex/--no-codex",
    "codex_setup",
    default=None,
    help="Generate or skip project-local Codex MCP config and hooks.",
)
@click.option(
    "--include-submodules",
    is_flag=True,
    default=False,
    help="Include git submodule directories (excluded by default).",
)
@click.option(
    "--all",
    "init_all",
    is_flag=True,
    default=False,
    help="In multi-repo mode, index all detected repos without prompting.",
)
@click.option(
    "--onboarding/--no-onboarding",
    "onboarding",
    default=True,
    help=(
        "Generate the curated Onboarding collection (Project Overview, "
        "Architecture Guide, Getting Started, Codebase Map, Key Concepts, "
        "How It Works, Development Guide, Active Landscape). Default: on. "
        "Slots with insufficient signal are skipped automatically."
    ),
)
@click.option(
    "--coverage",
    "coverage_pct",
    type=float,
    default=None,
    metavar="PCT",
    help=(
        "Documentation coverage as a fraction of repo files (e.g. 0.10, 0.20, "
        "0.50). Bypasses the interactive coverage chooser. Default when "
        "interactive: prompt; otherwise 0.20."
    ),
)
@click.option(
    "--harvest-decisions/--no-harvest-decisions",
    "harvest_decisions",
    default=True,
    help=(
        "Harvest candidate architectural decisions from LLM page generation "
        "(file pages). Each harvested decision is verified against the file's "
        "source before storage. The model emits a decision only on a genuine "
        "hit, so the token cost lands only on files that carry one. Default: on."
    ),
)
def init_command(
    path: str | None,
    provider_name: str | None,
    model: str | None,
    embedder_name: str | None,
    skip_tests: bool,
    skip_infra: bool,
    dry_run: bool,
    yes: bool,
    resume: bool,
    force: bool,
    concurrency: int,
    reasoning: str | None,
    test_run: bool,
    index_only: bool,
    run_mode: str,
    exclude: tuple[str, ...],
    commit_limit: int | None,
    follow_renames: bool,
    no_claude_md: bool,
    agents_md: bool | None,
    codex_setup: bool | None,
    include_submodules: bool,
    init_all: bool,
    onboarding: bool,
    coverage_pct: float | None,
    harvest_decisions: bool,
) -> None:
    """Generate wiki documentation for a codebase.

    PATH defaults to the current directory.
    Use --index-only to run ingestion (AST, graph, git, dead code) without LLM generation.
    Use --mode fast for a quick graph + essential-git index of a very large repo.
    """
    # --mode fast is a graph + essential-git index with no LLM work, so it
    # implies index-only on the CLI side; the orchestrator mode below switches
    # the git tier to ESSENTIAL.
    if run_mode == "fast":
        index_only = True
    start = time.monotonic()
    repo_path = resolve_repo_path(path)

    if not repo_path.is_dir():
        raise click.ClickException(f"Not a directory: {repo_path}")

    # ---- Workspace detection ----
    # If the path contains multiple git repos (and is not itself a single repo),
    # branch into the multi-repo workspace flow.
    from repowise.core.workspace import scan_for_repos

    scan = scan_for_repos(repo_path, include_submodules=include_submodules)
    if len(scan.repos) > 1:
        _workspace_init(
            scan=scan,
            init_all=init_all,
            exclude_patterns=list(exclude),
            commit_limit=commit_limit,
            follow_renames=follow_renames,
            no_claude_md=no_claude_md,
            agents_md=agents_md,
            codex_setup=codex_setup,
            include_submodules=include_submodules,
            provider_name=provider_name,
            model=model,
            embedder_name=embedder_name,
            index_only=index_only,
            skip_tests=skip_tests,
            skip_infra=skip_infra,
            concurrency=concurrency,
            reasoning=reasoning,
            test_run=test_run,
            yes=yes,
            dry_run=dry_run,
            resume=resume,
            force=force,
            onboarding=onboarding,
            coverage_pct=coverage_pct,
            harvest_decisions=harvest_decisions,
            run_mode=run_mode,
        )
        return

    # If a single repo was found inside the given directory (not at root),
    # redirect to it so the user doesn't have to specify the exact path.
    if len(scan.repos) == 1 and scan.repos[0].path != repo_path:
        repo_path = scan.repos[0].path

    ensure_repowise_dir(repo_path)
    load_dotenv(repo_path)

    # Suppress library/structlog output — progress bars are the only output needed.
    setup_logging_silence()

    # On --resume, continue the prior run's git tier so a resumed fast index
    # doesn't silently fall back to the expensive FULL tier (issue #341). Done
    # before the interactive gate so a resume never re-prompts for the mode.
    run_mode = effective_run_mode_for_resume(repo_path, run_mode, resume)
    if run_mode == "fast":
        index_only = True

    # ---- Interactive mode (TTY, no explicit flags) ----
    is_interactive = sys.stdin.isatty() and provider_name is None and not index_only

    # Tiered doc generation cap (set in advanced mode); None = every selected
    # file page is a full-LLM tier-1 page (unchanged behaviour).
    tier1_top_n: int | None = None

    # Pre-scan for interactive mode — fast stats to inform choices
    scan_info = None
    if is_interactive:
        print_banner(console, repo_name=repo_path.name)
        with console.status("  Scanning repository…", spinner="dots"):
            scan_info = quick_repo_scan(repo_path)
        print_scan_summary(console, scan_info)
        mode = interactive_mode_select(console)

        if mode == "index_only":
            index_only = True
            # On a large repo, an index-only run is exactly the case where the
            # fast tier (essential git, no blame/co-change) pays off — offer it,
            # defaulting to yes since docs are already opted out.
            if (
                run_mode != "fast"
                and should_offer_fast_mode(scan_info)
                and interactive_fast_mode_offer(console, scan_info, default_fast=True)
            ):
                run_mode = "fast"
        elif mode == "advanced":
            selection = interactive_provider_config_select(
                console,
                model,
                reasoning,
                repo_path=repo_path,
            )
            provider_name = selection.provider_name
            model = selection.model
            reasoning = selection.reasoning
            adv = interactive_advanced_config(
                console,
                scan=scan_info,
                allow_fast=True,
                prompt_reasoning=False,
            )
            commit_limit = adv["commit_limit"]
            follow_renames = adv["follow_renames"]
            skip_tests = adv["skip_tests"]
            skip_infra = adv["skip_infra"]
            concurrency = adv["concurrency"]
            reasoning = adv.get("reasoning") or reasoning
            exclude = adv["exclude"]
            test_run = adv["test_run"]
            embedder_name = adv.get("embedder") or embedder_name
            include_submodules = adv.get("include_submodules", include_submodules)
            run_mode = adv.get("run_mode", run_mode)
            tier1_top_n = adv.get("tier1_top_n")
            if run_mode == "fast":
                index_only = True
        else:
            selection = interactive_provider_config_select(
                console,
                model,
                reasoning,
                repo_path=repo_path,
            )
            provider_name = selection.provider_name
            model = selection.model
            reasoning = selection.reasoning
            # Full mode picked, but on a large repo offer the quick path too.
            # Default no here — the user explicitly asked for docs.
            if (
                run_mode != "fast"
                and should_offer_fast_mode(scan_info)
                and interactive_fast_mode_offer(console, scan_info, default_fast=False)
            ):
                run_mode = "fast"
                index_only = True

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
        prompt_for_project_files=is_interactive and not index_only,
    )

    # Merge exclude_patterns from config.yaml and --exclude/-x flags
    config = load_config(repo_path)
    language = config.get("language", "en")
    resolved_reasoning = resolve_reasoning(reasoning, config)
    exclude_patterns: list[str] = list(config.get("exclude_patterns") or []) + list(exclude)

    # Resolve commit limit: CLI flag → config.yaml → default (500)
    resolved_commit_limit: int = commit_limit or config.get("commit_limit") or 500
    resolved_commit_limit = max(1, min(resolved_commit_limit, 5000))
    if commit_limit is not None:
        config["commit_limit"] = resolved_commit_limit

    # Resolve follow_renames: CLI flag → config.yaml
    resolved_follow_renames: bool = follow_renames or config.get("follow_renames", False)
    if follow_renames:
        config["follow_renames"] = True

    embedder_name_resolved = resolve_embedder(embedder_name)

    # ---- Resolve provider ----
    provider = None
    decision_provider = None

    if index_only:
        try:
            if (
                provider_name
                or (sys.stdin.isatty() is False)
                or any(
                    os.environ.get(k)
                    for k in (
                        "GEMINI_API_KEY",
                        "GOOGLE_API_KEY",
                        "OPENAI_API_KEY",
                        "ANTHROPIC_API_KEY",
                    )
                )
            ):
                decision_provider = resolve_provider(provider_name, model, repo_path)
        except Exception:
            pass

        has_provider = decision_provider is not None
        if is_interactive:
            print_index_only_intro(console, has_provider=has_provider)
        else:
            console.print(f"[bold]repowise index-only[/bold] — {repo_path}")
            console.print("[yellow]Skipping LLM page generation (--index-only)[/yellow]")
            if decision_provider:
                console.print(
                    f"Decision extraction provider: [cyan]{decision_provider.provider_name}[/cyan]"
                )
    else:
        if not is_interactive and provider_name is None and sys.stdin.isatty():
            from repowise.cli.ui import interactive_provider_config_select as _ipcs

            selection = _ipcs(console, model, reasoning, repo_path=repo_path)
            provider_name = selection.provider_name
            model = selection.model
            reasoning = selection.reasoning

        provider = resolve_provider(provider_name, model, repo_path)
        # resolve_provider / interactive provider selection may have just set
        # the API key in os.environ. Re-resolve the embedder so the
        # display (and the embed path below) honors the key the user just
        # pasted, rather than the pre-prompt "mock" fallback.
        embedder_name_resolved = resolve_embedder(embedder_name)
        if not is_interactive:
            console.print(f"[bold]repowise init[/bold] — {repo_path}")
        console.print(
            f"  Provider: [cyan]{provider.provider_name}[/cyan] / Model: [cyan]{provider.model_name}[/cyan]"
        )
        console.print(f"  Embedder: [cyan]{embedder_name_resolved}[/cyan]")
        if language != "en":
            console.print(f"  Language: [cyan]{language}[/cyan]")
        if resolved_reasoning != "auto":
            console.print(f"  Reasoning: [cyan]{resolved_reasoning}[/cyan]")

        # Validate provider connection
        from repowise.core.providers.llm.base import ProviderError

        with console.status("  Verifying provider connection…", spinner="dots"):
            try:
                run_async(
                    provider.generate(
                        "You are a test.",
                        "Reply with OK.",
                        max_tokens=50,
                        reasoning=resolved_reasoning,
                    )
                )
            except ProviderError as exc:
                raise click.ClickException(f"Provider validation failed: {exc}") from exc
        console.print("  [green]✓[/green] Provider connection verified")

    # ---- Phase 1 & 2: Ingestion + Analysis (always) ----
    total_phases = 3 if index_only else 4
    # Tracks whether the user declined the LLM cost gate. When True we
    # skip generation but still persist the index/graph/git/dead-code so
    # the run isn't wasted, and propagate the choice to state.docs_enabled
    # so subsequent updates default to index-only.
    cost_declined = False
    llm_client = provider if not index_only else decision_provider

    from repowise.core.pipeline import PhaseTimingRecorder, run_pipeline
    from repowise.core.pipeline.modes import OrchestratorMode

    orchestrator_mode = OrchestratorMode.FAST if run_mode == "fast" else OrchestratorMode.STANDARD

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MaybeCountColumn(),
        TimeElapsedColumn(),
        TextColumn("[green]${task.fields[cost]:.3f}[/green]"),
        console=console,
    ) as progress_bar:
        rich_callback = RichProgressCallback(progress_bar, console)
        # Wrap the Rich callback so we can record per-phase wall-clock
        # durations without changing the pipeline API. Timings get
        # persisted to state.json below.
        callback = PhaseTimingRecorder(rich_callback)

        # Always run ingestion + analysis first (generate_docs=False).
        # Generation happens separately after cost confirmation.
        _prev_state = load_state(repo_path)
        _prev_kg_fp = (
            _prev_state.get("knowledge_graph", {}).get("fingerprint") if not force else None
        )

        async def _index_with_resume() -> Any:
            # Create the engine, session factory, and repository row *before*
            # the pipeline (all in this one event loop) so the resume
            # controller has a stable Repository.id to checkpoint against —
            # fixing the old str(repo_path) FK wiring — and so an interrupt
            # mid-run leaves a resumable, persisted index behind. Skipped on a
            # dry run, which must not touch the database at all.
            controller = None
            engine = None
            if not dry_run:
                controller, engine = await build_resume_controller(repo_path, resume=resume)
            try:
                return await run_pipeline(
                    repo_path,
                    commit_depth=resolved_commit_limit,
                    follow_renames=resolved_follow_renames,
                    skip_tests=skip_tests,
                    skip_infra=skip_infra,
                    exclude_patterns=exclude_patterns if exclude_patterns else None,
                    include_submodules=include_submodules,
                    generate_docs=False,
                    llm_client=llm_client,
                    concurrency=concurrency,
                    test_run=test_run,
                    mode=orchestrator_mode,
                    progress=callback,
                    existing_kg_fingerprint=_prev_kg_fp,
                    resume_controller=controller,
                )
            finally:
                if engine is not None:
                    await engine.dispose()

        # Make the long synchronous index/analysis phases interruptible: the
        # first Ctrl-C unwinds them cleanly (the INDEX checkpoint already on
        # disk is reused on the next --resume), a second forces a hard quit.
        from repowise.core.cancellation import PipelineCancelled, cancellation_scope

        try:
            with cancellation_scope():
                result = run_async(_index_with_resume())
        except (PipelineCancelled, KeyboardInterrupt):
            console.print(
                "\n[yellow]Interrupted.[/] Indexed work so far has been saved — "
                "run [bold]repowise init --resume[/] to continue where it stopped."
            )
            return

    # Surface per-phase timing data to the caller — both for the
    # state.json persistence below and for any future "profile" tooling
    # that wants to introspect a run.
    phase_timings: dict[str, float] = callback.timings

    # ---- Analysis summary (shown between analysis and generation) ----
    show_analysis_summary(result)

    # ---- Phase 3: Generation (full mode only) ----
    if not index_only:
        gen_stop, cost_declined = _run_generation_phase(
            repo_path=repo_path,
            result=result,
            provider=provider,
            total_phases=total_phases,
            concurrency=concurrency,
            language=language,
            resolved_reasoning=resolved_reasoning,
            onboarding=onboarding,
            tier1_top_n=tier1_top_n,
            harvest_decisions=harvest_decisions,
            coverage_pct=coverage_pct,
            yes=yes,
            dry_run=dry_run,
            skip_tests=skip_tests,
            skip_infra=skip_infra,
            embedder_name_resolved=embedder_name_resolved,
            resume=resume,
        )
        if gen_stop:
            return

    # ---- Persistence ----
    # `cost_declined` short-circuits any further LLM work for the rest of
    # this run, so persistence/state below treat it as index-only.
    effective_index_only = index_only or cost_declined
    if effective_index_only:
        print_phase_header(console, 3, total_phases, "Persistence", "Saving to database")
    else:
        print_phase_header(
            console, 4, total_phases, "Persistence", "Saving to database and building search index"
        )

    with console.status("  Persisting to database…", spinner="dots"):
        run_async(persist_result(result, repo_path))
    console.print("  [green]✓[/green] Database updated")

    # Persist the onboarding choice so subsequent `repowise update` runs
    # honor it without re-passing the flag. Default True is omitted to keep
    # config files tidy — only the override is recorded.
    if not onboarding:
        save_config_partial(repo_path, enable_onboarding=False)

    # ---- Post-run: config, state, MCP, editor project files ----
    if commit_limit is not None:
        save_config_partial(repo_path, commit_limit=resolved_commit_limit)

    write_editor_project_files(
        console,
        repo_path,
        options=editor_options,
    )
    register_editor_clients(console, repo_path)

    # ---- State (always) ----
    # Even in index-only mode we persist `last_sync_commit` so that a
    # subsequent `repowise update` (e.g. fired by the post-commit hook) has
    # a baseline to diff against. Without this, index-only users hit
    # "No previous sync found" on every update.
    head = get_head_commit(repo_path)
    base_state = load_state(repo_path)
    base_state["last_sync_commit"] = head
    base_state["docs_enabled"] = not effective_index_only and provider is not None
    # Record the git tier this run indexed so a later --resume continues the
    # same tier instead of silently upgrading ESSENTIAL → FULL (issue #341).
    base_state["run_mode"] = run_mode
    base_state["git_tier"] = git_tier_for_run_mode(run_mode)
    if phase_timings:
        base_state["phase_timings"] = phase_timings
    kg = getattr(result, "knowledge_graph_result", None)
    if kg is not None:
        base_state["knowledge_graph"] = build_kg_state(kg)
        save_knowledge_graph_json(repo_path, kg)
    if effective_index_only or provider is None:
        # Index-only mode skips save_config(); persist exclude_patterns/commit_limit here.
        save_config_partial(
            repo_path,
            exclude_patterns=exclude_patterns if exclude_patterns else None,
            commit_limit=resolved_commit_limit if commit_limit is not None else None,
        )
        # Fingerprint after config writes so the first update doesn't false-positive.
        base_state["config_fingerprint"] = config_fingerprint(repo_path)
        save_state(repo_path, base_state)

    # ---- State + config (full mode only) ----
    if not effective_index_only and provider:
        save_full_state_and_config(
            repo_path=repo_path,
            result=result,
            provider=provider,
            phase_timings=phase_timings,
            embedder_name_resolved=embedder_name_resolved,
            exclude_patterns=exclude_patterns,
            commit_limit=commit_limit,
            resolved_commit_limit=resolved_commit_limit,
            resolved_reasoning=resolved_reasoning,
        )

    # ---- Completion panel ----
    show_completion(
        repo_path=repo_path,
        result=result,
        start=start,
        effective_index_only=effective_index_only,
        run_mode=run_mode,
        provider=provider,
    )

    # Offer to install post-commit hook (both index-only and full modes)
    offer_hook_install(console, [repo_path])
