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

from repowise.cli._setup import configure_cli_logging
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
from repowise.cli.providers.embedders import embedder_was_requested as _embedder_was_requested
from repowise.cli.state_persistence import build_kg_state, save_knowledge_graph_json
from repowise.cli.ui import (
    BRAND,
    BRAND_STYLE,
    OWL_SPINNER,
    MaybeCountColumn,
    RichProgressCallback,
    interactive_advanced_config,
    interactive_customize_offer,
    interactive_fast_mode_offer,
    interactive_generate_docs_toggle,
    interactive_mode_select,
    interactive_provider_config_select,
    load_dotenv,
    print_banner,
    print_index_only_intro,
    print_phase_header,
    print_scan_summary,
    prompt_wiki_style,
    quick_repo_scan,
    should_offer_fast_mode,
)
from repowise.core.docs_mode import docs_mode_state_fields, resolve_docs_mode
from repowise.core.generation.languages import SUPPORTED_LANGUAGES
from repowise.core.generation.styles import DEFAULT_STYLE, list_styles, resolve_style
from repowise.core.reasoning import REASONING_MODES

from ._interactive import offer_distill_rewrite_hook, offer_hook_install
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


def _record_init_outcome(
    *,
    result: Any,
    effective_index_only: bool,
    run_mode: str,
    provider: Any,
    embedder_name_resolved: str,
) -> None:
    """Attach an anonymous shape-of-the-index outcome to the ``command_run`` event.

    Coarse buckets + enums only (file-count bucket, docs mode, run mode, top
    language, provider/embedder names) — no repo names, paths, or exact counts.
    Best-effort: never let telemetry break the command's happy path.
    """
    try:
        from repowise.cli.platform import telemetry

        outcome: dict[str, Any] = {
            "outcome": "success",
            "index_only": bool(effective_index_only),
            "run_mode": run_mode,
            "file_count_bucket": telemetry.bucket_count(getattr(result, "file_count", 0) or 0),
            "symbol_count_bucket": telemetry.bucket_count(getattr(result, "symbol_count", 0) or 0),
        }

        lang_dist = getattr(
            getattr(result, "repo_structure", None), "root_language_distribution", None
        )
        if isinstance(lang_dist, dict) and lang_dist:
            outcome["top_language"] = max(lang_dist.items(), key=lambda kv: kv[1])[0]

        # docs_mode carries the same three values as the state field of that
        # name. It used to be a bool, which collapsed "template wiki" and "no
        # wiki" into one number, and template runs are the ones worth counting.
        pages = getattr(result, "generated_pages", None) or []
        det = sum(1 for p in pages if getattr(p, "provider_name", "") == "template")
        if not effective_index_only and provider is not None:
            outcome["docs_mode"] = "llm"
            outcome["provider"] = getattr(provider, "provider_name", None)
            outcome["model"] = getattr(provider, "model_name", None)
        else:
            outcome["docs_mode"] = "deterministic" if pages else "none"
        if pages:
            outcome["pages_bucket"] = telemetry.bucket_count(len(pages))
            outcome["deterministic_pages_bucket"] = telemetry.bucket_count(det)

        if embedder_name_resolved:
            outcome["embedder"] = embedder_name_resolved

        telemetry.add_command_outcome(**{k: v for k, v in outcome.items() if v is not None})
    except Exception:
        return


def _run_deterministic_generation_phase(
    *,
    repo_path: Path,
    result: Any,
    total_phases: int,
    concurrency: int,
    language: str,
    onboarding: bool,
    wiki_style: str,
    embedder_name_resolved: str,
    embedder_was_requested: bool,
    resume: bool,
) -> str:
    """Render the whole wiki from templates, for ``init --index-only``.

    Every page type has a deterministic renderer, so an index-only run is no
    longer a wiki-less index: it is a complete wiki whose pages are derived
    from structure rather than written by a model. There is nothing to
    estimate and nothing to gate, since no provider is involved and the run
    costs nothing, so this skips the coverage chooser the LLM phase runs.

    Returns the embedder actually used, which the caller persists so a later
    ``repowise update`` embeds the same way rather than re-deciding.
    """
    from repowise.core.generation import GenerationConfig
    from repowise.core.providers.llm.template import TemplateProvider

    # This mode is sold as "no key, no spend", and embedding 2000+ pages
    # through a hosted embedder is a real bill. ``resolve_embedder`` infers one
    # from any LLM key it finds in the environment, which is the right default
    # for a run that is already paying a model and the wrong one here: nobody
    # who typed --index-only asked to be charged. So a hosted embedder is used
    # only when the user named it, through --embedder or REPOWISE_EMBEDDER.
    # Anything else falls back to the mock, which keeps full-text search
    # working and leaves semantic search to be built later with
    # ``repowise reindex``.
    hosted = embedder_name_resolved not in ("mock", "ollama")
    embedder = "mock" if hosted and not embedder_was_requested else embedder_name_resolved

    print_phase_header(
        console,
        3,
        total_phases,
        "Generation",
        "Building wiki pages from the code's structure (no model, no cost)",
    )
    # Both knobs only reach a model. Templates carry their own English prose
    # and render without the style directive, so saying so beats letting the
    # user find out from the output (and beats recording a style the pages do
    # not have, which would leave `restyle` thinking there is nothing to do).
    if language != "en":
        console.print(
            f"  [dim]Templates are written in English, so [bold]--language "
            f"{language}[/bold] has no effect here. Run [bold]repowise update "
            "--full[/bold] to write the wiki with a model in that language.[/dim]"
        )
    if wiki_style != DEFAULT_STYLE:
        console.print(
            f"  [dim]Wiki styles shape how a model writes, so [bold]--wiki-style "
            f"{wiki_style}[/bold] has no effect here.[/dim]"
        )
    if embedder != embedder_name_resolved:
        console.print(
            f"  [dim]Not embedding with [bold]{embedder_name_resolved}[/bold]: it was "
            "inferred from a key in your environment, and this run is meant to cost "
            "nothing. Pass [bold]--embedder "
            f"{embedder_name_resolved}[/bold] if you want it.[/dim]"
        )

    gen_config = GenerationConfig(
        deterministic=True,
        max_concurrency=concurrency,
        language=language,
        enable_onboarding=onboarding,
        wiki_style=wiki_style,
    )
    run_repo_generation(
        repo_path=repo_path,
        result=result,
        provider=TemplateProvider(),
        gen_config=gen_config,
        concurrency=concurrency,
        embedder_name_resolved=embedder,
        resume=resume,
        verbose=True,
    )
    return embedder


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
    tier2_tail_enabled: bool,
    harvest_decisions: bool,
    wiki_style: str,
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

    gen_config = GenerationConfig.from_repo_config(
        load_config(repo_path),
        max_concurrency=concurrency,
        language=language,
        reasoning=resolved_reasoning,
        enable_onboarding=onboarding,
        tier1_top_n=tier1_top_n,
        tier2_tail_enabled=tier2_tail_enabled,
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

    # Warn when a local provider runs with default concurrency
    if provider.provider_name in ("ollama", "codex_cli", "opencode") and concurrency > 4:
        console.print(
            f"  [yellow]Warning:[/yellow] {provider.provider_name} is a local provider "
            f"running with concurrency={concurrency}. "
            f"If you see timeout errors, try [bold]--concurrency 1[/bold]."
        )

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

    if cost_gate_declined(est, yes=yes, message="  Write the wiki with the model at this cost?"):
        console.print(
            "[yellow]Not writing it with the model.[/yellow] "
            "[dim]Future `repowise update` runs stay index-only, so the "
            "post-commit hook won't start a model run on its own.[/dim]"
        )
        return False, True

    # Persist the tiering knobs so `repowise update` regenerates with the same
    # coverage settings (save_config later round-trips and preserves these).
    # save_config_partial skips None, so a no-cap tier1 stays unwritten.
    from repowise.cli.helpers import save_config_partial

    save_config_partial(
        repo_path,
        tier1_top_n=tier1_top_n,
        tier2_tail_enabled=tier2_tail_enabled,
    )

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
        "deepseek, kimi, ollama, litellm, codex_cli, opencode, mock)."
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
@click.option("--concurrency", type=int, default=10, help="Max concurrent LLM calls.")
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
    help="Build the wiki from structure instead of writing it with a model. No key, no spend.",
)
@click.option(
    "--docs",
    "docs_opt",
    type=click.Choice(["llm", "deterministic"]),
    default=None,
    help=(
        "How to produce the wiki: 'llm' writes it with a model (needs a key), "
        "'deterministic' renders it from structure for free. Same choice as "
        "--index-only, named for what it does. Use --mode fast for no wiki at all."
    ),
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
    help="Max commits to analyze per file and for co-change detection (default: 500, max: 10000). Saved to config.",
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
    "--distill-hook/--no-distill-hook",
    "distill_hook",
    default=None,
    help=(
        "Install the Claude Code command-rewrite hook that routes noisy "
        "commands (tests, builds, git, searches) through `repowise distill` "
        "for compact output. Default: ask when interactive; skip otherwise. "
        "In workspace mode the verdict applies to every selected repo."
    ),
)
@click.option(
    "--include-submodules",
    is_flag=True,
    default=False,
    help="Include git submodule directories (excluded by default).",
)
@click.option(
    "--no-workspace",
    "no_workspace",
    is_flag=True,
    default=False,
    help=(
        "Force single-repo mode even when invoked from a workspace root. "
        "Indexes only the target PATH and skips workspace detection."
    ),
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
    "--coverage-report",
    "coverage_report",
    type=click.Path(exists=True, dir_okay=False),
    multiple=True,
    metavar="PATH",
    help=(
        "Test-coverage report(s) to ingest (lcov / Cobertura / Clover). "
        "Repeatable. When omitted, common locations (coverage/lcov.info, "
        "**/cobertura.xml, ...) are auto-discovered. Distinct from --coverage, "
        "which controls documentation breadth."
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
@click.option(
    "--wiki-style",
    "wiki_style",
    type=click.Choice([s.name for s in list_styles()]),
    default=None,
    help=(
        "Documentation voice/density for generated pages: "
        "comprehensive (default), caveman (token-condensed, AI-first), "
        "reference (API-manual), tutorial (beginner-friendly). When omitted in "
        "an interactive full run you'll be prompted; otherwise comprehensive."
    ),
)
@click.option(
    "--language",
    "language_opt",
    type=click.Choice(sorted(SUPPORTED_LANGUAGES)),
    default=None,
    metavar="CODE",
    help=(
        "Output language for generated wiki pages (e.g. en, zh, ru, hi). "
        "Code, file paths, and symbol names stay untranslated. Saved to "
        "config so `update` keeps the language. Default: en."
    ),
)
@click.option(
    "--seed-from",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=str),
    help=(
        "Seed the index from an existing base-branch checkout to skip indexing "
        "unmodified files. Rarely needed: inside a linked git worktree the base "
        "checkout is detected and seeded automatically."
    ),
)
@click.option(
    "--no-seed",
    is_flag=True,
    default=False,
    help="Disable worktree auto-seeding and run a full init even inside a linked worktree.",
)
@click.option(
    "--no-cost-tracking",
    is_flag=True,
    default=False,
    help="Skip DB-backed LLM cost tracking for this run.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help=(
        "Show per-phase internals plus debug logs. Without it, debug/info "
        "logging is suppressed so the progress bar is the only output."
    ),
)
@click.option(
    "--progress",
    type=click.Choice(["rich", "json"]),
    default="rich",
    help="Progress output style.",
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
    docs_opt: str | None,
    run_mode: str,
    exclude: tuple[str, ...],
    commit_limit: int | None,
    follow_renames: bool,
    no_claude_md: bool,
    seed_from: str | None,
    no_seed: bool,
    agents_md: bool | None,
    codex_setup: bool | None,
    distill_hook: bool | None,
    include_submodules: bool,
    no_workspace: bool,
    init_all: bool,
    onboarding: bool,
    coverage_pct: float | None,
    coverage_report: tuple[str, ...],
    harvest_decisions: bool,
    wiki_style: str | None,
    language_opt: str | None,
    no_cost_tracking: bool,
    verbose: bool,
    progress: str,
) -> None:
    """Generate wiki documentation for a codebase.

    PATH defaults to the current directory.
    Use --docs deterministic (or --index-only) to render the wiki from structure
    with no model and no key; --docs llm writes it with a model.
    Use --mode fast for a quick graph + essential-git index of a very large repo.
    """
    # ``--docs`` is the same switch as ``--index-only`` named for what it
    # produces rather than what it skips, since an index-only run has rendered
    # a full wiki since #975. Kept as two spellings because --index-only is in
    # every existing script and doc; they must not disagree.
    if docs_opt is not None:
        if index_only and docs_opt == "llm":
            raise click.UsageError("--docs llm contradicts --index-only. Pass one.")
        index_only = docs_opt == "deterministic"
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
    # branch into the multi-repo workspace flow.  --no-workspace bypasses this
    # entirely and forces single-repo mode regardless of what scan finds.
    from repowise.core.workspace import scan_for_repos

    scan = scan_for_repos(repo_path, include_submodules=include_submodules)

    # ---- Worktree seeding ----
    # Explicit --seed-from wins. Otherwise, an unindexed linked worktree
    # auto-seeds from its base checkout (derived via --git-common-dir, no
    # path needed) when that base holds a healthy index. --no-seed forces a
    # cold init; any failed validation falls back to full init with a notice.
    from repowise.cli.worktree import (
        base_is_seedable,
        detect_worktree_base,
        seed_index_from_base,
    )

    seed_base: Path | None = None
    if seed_from:
        seed_base = Path(seed_from).resolve()
        if seed_base == repo_path.resolve():
            raise click.ClickException("--seed-from cannot be the same as the target directory.")
    elif not no_seed and not (repo_path / ".repowise" / "state.json").exists():
        detected = detect_worktree_base(repo_path)
        if detected is not None and base_is_seedable(detected):
            seed_base = detected
            console.print(
                f"[dim]\\[worktree][/dim] Linked worktree of {detected} detected; "
                f"seeding its index."
            )

    if seed_base is not None:
        seed_root = scan.root if getattr(scan, "root", None) else repo_path
        seeded = seed_index_from_base(
            root=seed_root,
            repo_paths=[r.path for r in scan.repos],
            seed_base=seed_base,
            include_submodules=include_submodules,
        )
        if seeded:
            console.print(
                "[green]Worktree index seeded successfully. Delegating to update...[/green]"
            )
            from repowise.cli.commands.update_cmd.command import run_update

            is_workspace = len(scan.repos) > 1 and not no_workspace

            # Delegate to update
            run_update(
                path=str(repo_path),
                provider_name=provider_name,
                model=model,
                since=None,
                reasoning=reasoning,
                cascade_budget=None,
                dry_run=dry_run,
                workspace=is_workspace,
                no_workspace=no_workspace,
                repo_alias=None,
                index_only=index_only,
                docs_flag=None,
                full=force,
                agents_md=agents_md,
                concurrency=concurrency,
                no_cost_tracking=no_cost_tracking,
                verbose=verbose,
                progress=progress,
            )
            return
    if len(scan.repos) > 1 and not no_workspace:
        _workspace_init(
            scan=scan,
            init_all=init_all,
            exclude_patterns=list(exclude),
            commit_limit=commit_limit,
            follow_renames=follow_renames,
            no_claude_md=no_claude_md,
            agents_md=agents_md,
            codex_setup=codex_setup,
            distill_hook=distill_hook,
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
            # Apply the chosen style uniformly across the workspace's repos
            # (no per-repo interactive prompt in the multi-repo flow).
            wiki_style=resolve_style(wiki_style).name,
            language=language_opt,
            run_mode=run_mode,
        )
        return

    # If a single repo was found inside the given directory (not at root),
    # redirect to it so the user doesn't have to specify the exact path.
    if len(scan.repos) == 1 and scan.repos[0].path != repo_path:
        repo_path = scan.repos[0].path

    ensure_repowise_dir(repo_path)
    load_dotenv(repo_path)

    # Quiet library/structlog output so the progress bars are the only output;
    # `-v` lets repowise's debug lines through for troubleshooting.
    configure_cli_logging(verbose=verbose)

    # On --resume, continue the prior run's git tier so a resumed fast index
    # doesn't silently fall back to the expensive FULL tier (issue #341). Done
    # before the interactive gate so a resume never re-prompts for the mode.
    run_mode = effective_run_mode_for_resume(repo_path, run_mode, resume)
    if run_mode == "fast":
        index_only = True

    # ---- Interactive mode (TTY, no explicit flags) ----
    # --yes forces non-interactive even on a TTY (mirrors the workspace path),
    # so a scripted `init -y` never blocks on the mode-selection menu.
    is_interactive = sys.stdin.isatty() and provider_name is None and not index_only and not yes

    # Tiered doc generation cap (set in advanced mode); None = every selected
    # file page is a full-LLM tier-1 page (unchanged behaviour).
    tier1_top_n: int | None = None

    # Deterministic coverage tail (Phase G): document every remaining source
    # file with a free, no-LLM page. On by default; only the advanced menu
    # can turn it off.
    tier2_tail_enabled: bool = True

    # Output language picked in the advanced-mode generation section; None
    # until chosen. Resolved below: flag > this > config.yaml > English.
    language_choice: str | None = None

    # The two orthogonal axes the interactive menu resolves: whether docs are
    # generated and whether we entered the advanced-config prompts. Initialized
    # for the non-interactive path (where the menu never runs); the menu
    # overrides them below. They gate the editor-files prompt further down.
    generate_docs = not index_only
    customize = False

    # Pre-scan for interactive mode — fast stats to inform choices
    scan_info = None
    if is_interactive:
        print_banner(console, repo_name=repo_path.name)
        with console.status("  Scanning repository…", spinner=OWL_SPINNER):
            scan_info = quick_repo_scan(repo_path)
        print_scan_summary(console, scan_info)

        # ``sys.stdin.isatty()`` is not a reliable answer to "can I read from
        # stdin". On Windows under Git Bash, ``repowise init < /dev/null``
        # reports a TTY and then reads EOF on the first question; the same goes
        # for some pty wrappers and ``docker run -t`` without -i. Agents drive
        # init through exactly those shapes, so the first prompt is treated as
        # the probe: if it cannot be answered, drop to the non-interactive path
        # and carry on rather than dying on a question nobody can hear. Only
        # EOFError is caught. A real Ctrl-C raises KeyboardInterrupt/Abort and
        # must still stop the run.
        mode = None
        try:
            mode = interactive_mode_select(console)
        except EOFError:
            is_interactive = False
            console.print(
                "\n[yellow]No answer available on stdin[/yellow] "
                "[dim]— continuing with defaults. Pass --yes to skip the "
                "questions, or --docs llm/deterministic to choose directly.[/dim]"
            )

        # Map the menu onto the two axes (docs on/off, customize yes/no):
        #   full       -> docs on,  optional customize
        #   index_only -> docs off, optional customize
        #   advanced   -> docs toggle, always customize
        if mode == "index_only":
            generate_docs = False
            customize = interactive_customize_offer(console, generate_docs=False)
        elif mode == "advanced":
            generate_docs = interactive_generate_docs_toggle(console)
            customize = True
        elif mode is not None:  # full
            generate_docs = True
            customize = interactive_customize_offer(console, generate_docs=True)
        if mode is not None:
            index_only = not generate_docs

        # Provider selection only when docs will be generated. Index-only runs
        # auto-detect a decision-extraction provider later without prompting.
        # ``is_interactive`` can have been cleared just above by an unanswerable
        # menu, in which case every remaining question here is unanswerable too.
        if generate_docs and is_interactive:
            selection = interactive_provider_config_select(
                console,
                model,
                reasoning,
                repo_path=repo_path,
            )
            provider_name = selection.provider_name
            model = selection.model
            reasoning = selection.reasoning

        if customize:
            adv = interactive_advanced_config(
                console,
                scan=scan_info,
                allow_fast=True,
                prompt_reasoning=False,
                generate_docs=generate_docs,
                wiki_style=wiki_style,
                language=language_opt,
            )
            # Indexing knobs (always present).
            commit_limit = adv["commit_limit"]
            follow_renames = adv["follow_renames"]
            skip_tests = adv["skip_tests"]
            skip_infra = adv["skip_infra"]
            exclude = adv["exclude"]
            include_submodules = adv.get("include_submodules", include_submodules)
            run_mode = adv.get("run_mode", run_mode)
            # Asked in both branches: an index-only run renders a wiki too,
            # and those pages embed like any other, so the answer applies
            # either way. Read outside the docs-only block or the index-only
            # run would show the user their choice and then ignore it.
            embedder_name = adv.get("embedder") or embedder_name
            # Generation knobs (only gathered when docs are on).
            if generate_docs:
                concurrency = adv["concurrency"]
                reasoning = adv.get("reasoning") or reasoning
                test_run = adv["test_run"]
                tier1_top_n = adv.get("tier1_top_n")
                tier2_tail_enabled = adv.get("tier2_tail_enabled", True)
                onboarding = adv.get("onboarding", onboarding)
                harvest_decisions = adv.get("harvest_decisions", harvest_decisions)
                if adv.get("wiki_style"):
                    wiki_style = adv["wiki_style"]
                if adv.get("language"):
                    language_choice = adv["language"]
            # Fast mode (picked in the indexing section) is a no-LLM index, so it
            # forces index-only even if the user had asked for docs.
            if run_mode == "fast":
                index_only = True
                generate_docs = False
        elif is_interactive:
            # No customization: still offer the fast first-index on large repos.
            # Default yes for an index-only run (docs already opted out), no for a
            # full run (the user explicitly asked for docs).
            if (
                run_mode != "fast"
                and should_offer_fast_mode(scan_info)
                and interactive_fast_mode_offer(console, scan_info, default_fast=not generate_docs)
            ):
                run_mode = "fast"
                index_only = True
                generate_docs = False

        # Wiki style: prompt for a docs run when not already chosen (via the
        # --wiki-style flag or the advanced generation section). Index-only runs
        # generate no pages, so the question is skipped. --yes uses the default.
        if generate_docs and wiki_style is None and not yes and is_interactive:
            wiki_style = prompt_wiki_style(console)

    # Resolve the effective style (CLI flag > interactive prompt > default) and
    # canonicalize it. Used by generation and persisted so update/restyle honor it.
    wiki_style = resolve_style(wiki_style).name

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
        # Prompt for CLAUDE.md / AGENTS.md / Codex setup whenever the user is
        # engaging interactively — either generating docs or customizing an
        # index-only run (the latter previously got no say).
        prompt_for_project_files=is_interactive and (generate_docs or customize),
    )

    # Merge exclude_patterns from config.yaml and --exclude/-x flags
    config = load_config(repo_path)
    # Output language: CLI flag > advanced-mode choice > config.yaml > English.
    language = language_opt or language_choice or config.get("language", "en")
    resolved_reasoning = resolve_reasoning(reasoning, config)
    exclude_patterns: list[str] = list(config.get("exclude_patterns") or []) + list(exclude)

    # Resolve commit limit: CLI flag → config.yaml → default (500)
    resolved_commit_limit: int = commit_limit or config.get("commit_limit") or 500
    resolved_commit_limit = max(1, min(resolved_commit_limit, 10000))
    if commit_limit is not None:
        config["commit_limit"] = resolved_commit_limit

    # Resolve follow_renames: CLI flag → config.yaml
    resolved_follow_renames: bool = follow_renames or config.get("follow_renames", False)
    if follow_renames:
        config["follow_renames"] = True

    embedder_name_resolved = resolve_embedder(embedder_name)
    embedder_was_requested = _embedder_was_requested(embedder_name, config.get("embedder"))

    # A template wiki overwrites a model-written one page for page: the upsert
    # replaces content and stamps provider_name="template", keeping the old
    # text only as a version snapshot. That is a fine outcome when it is what
    # the user meant and a bad surprise when they were reaching for a re-index,
    # so ask. Non-interactive runs refuse rather than guess.
    # ``--yes`` and ``--force`` both mean "do not ask me", which is the answer
    # here as much as at the cost gate.
    _prior_state = load_state(repo_path)
    # Whether this repo has ever been indexed, captured before the run writes
    # its own state. Drives the completion panel's MCP note (a first index tells
    # the user to restart Claude Code to load the tools; a re-index does not).
    _first_index = not _prior_state.get("last_sync_commit")
    _prior_docs_mode = resolve_docs_mode(_prior_state)
    if index_only and _prior_docs_mode == "llm" and not (force or yes):
        console.print(
            "\n[yellow]This repo already has a model-written wiki.[/yellow] "
            "Indexing without a model\nrewrites every page from templates; the "
            "written versions stay in page history."
        )
        if not sys.stdin.isatty():
            raise click.ClickException(
                "Refusing to replace a model-written wiki with template pages. "
                "Re-run with --yes to confirm, or drop --index-only."
            )
        if not click.confirm("  Replace the written wiki with template pages?", default=False):
            console.print("[dim]Nothing changed.[/dim]")
            return

    # ---- Resolve provider ----
    provider = None
    decision_provider = None
    # Set when a full run found no provider at all and fell back to the
    # template renderer. Treated exactly like ``--index-only`` from the
    # generation phase onward.
    no_provider = False

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
            console.print(
                "[yellow]Building the wiki from structure[/yellow] [dim]— no model, no spend.[/dim]"
            )
            if decision_provider:
                console.print(
                    f"Decision extraction provider: [cyan]{decision_provider.provider_name}[/cyan]"
                )
    else:
        # No prompt here. ``is_interactive`` (line ~800) is already false only
        # when the user passed --provider, --index-only or --yes, or stdin is
        # not a terminal — so the old fallback picker in this branch fired
        # exactly on ``--yes``, which means "do not ask me". On shells where
        # isatty() claims a terminal it cannot actually read from (Windows
        # mintty, ``docker run -t`` without -i) that prompt hit EOF and killed
        # the run instead. A --yes run with no key now lands in the template
        # wiki below rather than in a question or a crash.
        try:
            provider = resolve_provider(provider_name, model, repo_path)
        except click.ClickException:
            # Nothing configured anywhere. Workspace init, workspace update
            # and the OSS server all render a template wiki in this exact
            # situation rather than refusing to run (#999); single-repo init
            # was the last path that still died on it. An explicitly named
            # --provider is a different question: the user asked for that
            # provider by name, so the resolution failure is the real answer.
            if provider_name is not None:
                raise
            no_provider = True
            if not is_interactive:
                console.print(f"[bold]repowise init[/bold] — {repo_path}")
            console.print(
                "[yellow]No model configured.[/yellow] Building the wiki from "
                "structure instead — no key, no spend.\n"
                "[dim]Set a key (or pass --provider) and run [bold]repowise "
                "update --full[/bold] to have a model write it.[/dim]"
            )
        # resolve_provider / interactive provider selection may have just set
        # the API key in os.environ. Re-resolve the embedder so the
        # display (and the embed path below) honors the key the user just
        # pasted, rather than the pre-prompt "mock" fallback.
        embedder_name_resolved = resolve_embedder(embedder_name)
        if not is_interactive and not no_provider:
            console.print(f"[bold]repowise init[/bold] — {repo_path}")
        if provider is not None:
            console.print(
                f"  Provider: [cyan]{provider.provider_name}[/cyan] / Model: [cyan]{provider.model_name}[/cyan]"
            )
        console.print(f"  Embedder: [cyan]{embedder_name_resolved}[/cyan]")
        if language != "en":
            console.print(f"  Language: [cyan]{language}[/cyan]")
        if resolved_reasoning != "auto":
            console.print(f"  Reasoning: [cyan]{resolved_reasoning}[/cyan]")

        # Validate provider connection. Nothing to verify when there is no
        # provider: this run renders from templates and never calls a model.
        if provider is not None:
            from repowise.core.providers.llm.base import ProviderError

            with console.status("  Verifying provider connection…", spinner=OWL_SPINNER):
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
    # Index-only generates too, it just renders templates instead of prompting
    # a model, so it is four phases like a full run. Fast mode is the one that
    # still stops at three: see the generation phase below for why.
    total_phases = 3 if run_mode == "fast" else 4
    # Tracks whether the user declined the LLM cost gate. When True we
    # skip generation but still persist the index/graph/git/dead-code so
    # the run isn't wasted, and propagate the choice to the persisted docs
    # mode so subsequent updates default to index-only.
    cost_declined = False
    llm_client = provider if not index_only else decision_provider

    from repowise.core.pipeline import PhaseTimingRecorder, run_pipeline
    from repowise.core.pipeline.modes import OrchestratorMode

    orchestrator_mode = OrchestratorMode.FAST if run_mode == "fast" else OrchestratorMode.STANDARD

    with Progress(
        SpinnerColumn(spinner_name=OWL_SPINNER, style=BRAND_STYLE),
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
                    coverage_report_paths=(
                        [Path(p) for p in coverage_report] if coverage_report else None
                    ),
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
            from repowise.cli.ui.mascot import EYES_SLEEPY, mini

            console.print(
                f"\n{mini(EYES_SLEEPY)} [yellow]Interrupted.[/] Indexed work so far has been "
                "saved — run [bold]repowise init --resume[/] to continue where it stopped."
            )
            return

    # Surface per-phase timing data to the caller — both for the
    # state.json persistence below and for any future "profile" tooling
    # that wants to introspect a run.
    phase_timings: dict[str, float] = callback.timings

    # ---- Analysis summary (shown between analysis and generation) ----
    show_analysis_summary(result)

    # ---- Phase 3: Generation ----
    # The embedder the template wiki was actually built with, persisted below
    # so `repowise update` reuses it. None means no template wiki was rendered.
    _index_only_embedder: str | None = None
    # Both modes generate. Index-only renders from templates; full mode picks
    # a coverage level, estimates the spend and prompts a model.
    #
    # Fast mode is the exception. It exists to get a very large repo indexed
    # quickly, and it is offered precisely because the repo is large, so
    # rendering and embedding a page per file is the cost it was chosen to
    # avoid. It also runs ESSENTIAL git, which would leave the git-derived
    # sections of those pages thinner than the pages claim. Fast stays a
    # graph-and-git index; `repowise update --full` is the way out of it.
    if index_only and run_mode == "fast":
        console.print(
            "  [dim]Skipping wiki generation in fast mode. Run "
            "[bold]repowise init[/bold] without [bold]--mode fast[/bold] to "
            "render it from structure, or [bold]repowise update --full[/bold] "
            "to write it with a model.[/dim]"
        )
    elif index_only or no_provider:
        # ``no_provider`` reaches here the same way ``--index-only`` does: a
        # full run that found no key still gets the whole template wiki.
        _index_only_embedder = _run_deterministic_generation_phase(
            repo_path=repo_path,
            result=result,
            total_phases=total_phases,
            concurrency=concurrency,
            language=language,
            onboarding=onboarding,
            wiki_style=wiki_style,
            embedder_name_resolved=embedder_name_resolved,
            embedder_was_requested=embedder_was_requested,
            resume=resume,
        )
    else:
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
            tier2_tail_enabled=tier2_tail_enabled,
            harvest_decisions=harvest_decisions,
            wiki_style=wiki_style,
            coverage_pct=coverage_pct,
            yes=yes,
            dry_run=dry_run,
            skip_tests=skip_tests,
            skip_infra=skip_infra,
            embedder_name_resolved=embedder_name_resolved,
            # Resume seeds "already done" from the page ids in the vector
            # store, and a completed template wiki put one there for every
            # file. Resuming against it would skip the entire model run and
            # say nothing, so a template wiki is never a run to continue.
            resume=resume and _prior_docs_mode != "deterministic",
        )
        if gen_stop:
            return
        if cost_declined and _prior_docs_mode == "llm":
            # The repo already has written pages. Declining the price of new
            # ones is not a request to replace the old ones with templates,
            # which is what the fallback below would do.
            cost_declined = False
            console.print(
                "  [dim]Keeping the wiki this repo already has. Nothing was "
                "generated and nothing was replaced.[/dim]"
            )
        elif cost_declined:
            # Declining the gate used to mean leaving with no wiki at all.
            # It only ever meant "not at that price", so fall back to the
            # free renderer rather than to nothing.
            console.print(
                "  [dim]Building the wiki from structure instead. Run "
                "[bold]repowise update --full[/bold] to write it with a "
                "model later.[/dim]"
            )
            _index_only_embedder = _run_deterministic_generation_phase(
                repo_path=repo_path,
                result=result,
                total_phases=total_phases,
                concurrency=concurrency,
                language=language,
                onboarding=onboarding,
                wiki_style=wiki_style,
                # The user has a provider and just declined a bill, not the
                # embedder they configured, so honour it either way.
                embedder_was_requested=True,
                embedder_name_resolved=embedder_name_resolved,
                resume=resume,
            )

    # ---- Persistence ----
    # `cost_declined` short-circuits any further LLM work for the rest of
    # this run, so persistence/state below treat it as index-only. So does
    # `no_provider`: there is no model to do the work either way.
    effective_index_only = index_only or cost_declined or no_provider
    print_phase_header(
        console,
        total_phases,
        total_phases,
        "Persistence",
        "Saving to database and building search index",
    )

    with console.status("  Persisting to database…", spinner=OWL_SPINNER):
        run_async(persist_result(result, repo_path))
    console.print("  [green]✓[/green] Database updated")

    # Persist the onboarding choice so subsequent `repowise update` runs
    # honor it without re-passing the flag. Default True is omitted to keep
    # config files tidy — only the override is recorded.
    if not onboarding:
        save_config_partial(repo_path, enable_onboarding=False)

    # Persist the wiki style so `repowise update` / `restyle` honor it without
    # re-passing the flag. Written before any config_fingerprint is computed
    # below so the first update doesn't false-positive on a config change. The
    # default is omitted to keep config files tidy — only an override is recorded.
    #
    # A template wiki records neither style nor language: it has neither, and
    # recording them would make `restyle` believe the pages are already in that
    # style and refuse the very run that would put them there.
    if wiki_style != DEFAULT_STYLE and not effective_index_only:
        save_config_partial(repo_path, wiki_style=wiki_style)

    # Persist the output language so `repowise update` regenerates changed
    # pages in the same language. Written only when the run's language differs
    # from what config.yaml already holds; the default stays unrecorded.
    if language != config.get("language", "en") and not effective_index_only:
        save_config_partial(repo_path, language=language)

    # ---- Post-run: config, state, MCP, editor project files ----
    if commit_limit is not None:
        save_config_partial(repo_path, commit_limit=resolved_commit_limit)

    write_editor_project_files(
        console,
        repo_path,
        options=editor_options,
    )
    register_editor_clients(console, repo_path)

    # Inherit the workspace's distill rewrite-hook verdict NOW, before the
    # config fingerprint below is computed. `repowise update` runs the same
    # backfill at its start; if init leaves the verdict unwritten, the first
    # update writes it, sees a fingerprint mismatch, and silently replaces
    # the incremental path with a full health re-score of every file.
    from repowise.cli.commands.workspace_cmd import inherit_workspace_distill_verdict

    inherit_workspace_distill_verdict(repo_path)

    # ---- State (always) ----
    # Even in index-only mode we persist `last_sync_commit` so that a
    # subsequent `repowise update` (e.g. fired by the post-commit hook) has
    # a baseline to diff against. Without this, index-only users hit
    # "No previous sync found" on every update.
    head = get_head_commit(repo_path)
    base_state = load_state(repo_path)
    base_state["last_sync_commit"] = head
    # A full run wrote its pages with a model. An index-only run (or one where
    # the cost gate was declined) fell back to the template renderer. Fast mode
    # is the one path that reaches here with no pages at all.
    if run_mode == "fast":
        _docs_mode = "none"
    elif not effective_index_only and provider is not None:
        _docs_mode = "llm"
    else:
        _docs_mode = "deterministic"
    base_state.update(docs_mode_state_fields(_docs_mode))
    # Only the full-mode path went through save_full_state_and_config, which
    # counts the pages in the database. An index-only run now produces pages
    # too, so it has to record its own count or `status` reports a wiki of
    # zero pages next to a docs mode that says there is one.
    base_state["total_pages"] = len(getattr(result, "generated_pages", None) or [])
    # Record the git tier this run indexed so a later --resume continues the
    # same tier instead of silently upgrading ESSENTIAL → FULL (issue #341).
    base_state["run_mode"] = run_mode
    base_state["git_tier"] = git_tier_for_run_mode(run_mode)
    # Record whether submodules were indexed so `repowise update` rebuilds
    # the graph with the same boundary semantics (same pattern as git_tier:
    # missing → False keeps legacy behavior for old state files).
    base_state["include_submodules"] = include_submodules
    if phase_timings:
        base_state["phase_timings"] = phase_timings
    kg = getattr(result, "knowledge_graph_result", None)
    if kg is not None:
        base_state["knowledge_graph"] = build_kg_state(kg)
        save_knowledge_graph_json(repo_path, kg)
    if effective_index_only or provider is None:
        # Index-only mode skips save_config(); persist exclude_patterns/commit_limit here.
        # The embedder rides along now that this mode produces pages: without
        # it `repowise update` would re-resolve from the environment and could
        # start embedding, at a different width, a store this run deliberately
        # built with the mock.
        # ``embedding_model`` rides with it, the way save_config() writes the
        # pair: `serve` pins the model from it, and the store-format upgrade
        # check reads it to notice an embedder change. Half the pair is not
        # enough for either.
        from repowise.cli.providers.embedders import resolve_embedding_model

        save_config_partial(
            repo_path,
            exclude_patterns=exclude_patterns if exclude_patterns else None,
            commit_limit=resolved_commit_limit if commit_limit is not None else None,
            embedder=_index_only_embedder,
            embedding_model=(
                resolve_embedding_model(_index_only_embedder) if _index_only_embedder else None
            ),
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
            include_submodules=include_submodules,
        )

    _record_init_outcome(
        result=result,
        effective_index_only=effective_index_only,
        run_mode=run_mode,
        provider=provider,
        embedder_name_resolved=embedder_name_resolved,
    )

    # Offer to install post-commit hook (both index-only and full modes)
    offer_hook_install(console, [repo_path], yes=yes)

    # Opt-in distill command-rewrite hook for Claude Code. The workspace flow
    # runs its own offer across all selected repos inside _workspace_init.
    offer_distill_rewrite_hook(console, [repo_path], distill_hook, yes=yes)

    # ---- Completion panel (last, so it reflects what setup actually did) ----
    # Snapshot the editor-setup state now that client registration and the two
    # hook offers above have run, so the "what's next" panel and MCP note react
    # to reality. `interactive` mirrors the offers' own gate: when False they
    # were skipped, so the panel is the only place their hooks surface.
    from repowise.cli.editor_setup import detect_editor_setup_outcome

    _setup_outcome = detect_editor_setup_outcome(
        repo_path,
        interactive=(sys.stdin.isatty() and not yes),
        first_index=_first_index,
    )
    show_completion(
        repo_path=repo_path,
        result=result,
        start=start,
        effective_index_only=effective_index_only,
        run_mode=run_mode,
        provider=provider,
        setup=_setup_outcome,
    )
