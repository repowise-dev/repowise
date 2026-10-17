"""``repowise init`` — full wiki generation for a repository.

This module owns the Click command and the single-repo orchestration. The
multi-repo path lives in :mod:`.workspace`; generation, persistence and console
rendering live in :mod:`.generation`, :mod:`.persistence` and :mod:`.reporting`
respectively, and are shared by both flows.
"""

from __future__ import annotations

import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import click
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from repowise.cli._setup import configure_cli_logging
from repowise.cli.commands._index_outcome import record_index_outcome, refresh_read_snapshots
from repowise.cli.editor_integrations.defaults import (
    get_default_disabled_project_files,
    get_default_integration_overrides,
    get_default_project_file_overrides,
)
from repowise.cli.editor_setup import (
    register_editor_clients,
    resolve_editor_setup_options,
    select_agents_interactively,
    write_editor_project_files,
)
from repowise.cli.errors import reasoned_error
from repowise.cli.helpers import (
    config_fingerprint,
    console,
    ensure_repowise_dir,
    get_head_commit,
    head_commit_ts,
    load_config,
    load_state,
    resolve_max_file_pages,
    resolve_provider,
    resolve_reasoning,
    resolve_repo_path,
    run_async,
    save_config_partial,
    save_state,
    warn,
)
from repowise.cli.providers import resolve_embedder
from repowise.cli.providers.embedders import embedder_was_requested as _embedder_was_requested
from repowise.cli.state_persistence import build_kg_state, save_knowledge_graph_json
from repowise.cli.ui import (
    BRAND,
    BRAND_STYLE,
    OK,
    OWL_SPINNER,
    VALUE,
    WARN,
    MaybeCountColumn,
    RichProgressCallback,
    agent_providers_set_up,
    interactive_advanced_config,
    interactive_customize_offer,
    interactive_fast_mode_offer,
    interactive_generate_docs_toggle,
    interactive_mode_select,
    interactive_provider_config_select,
    load_dotenv,
    print_banner,
    print_phase_header,
    print_scan_summary,
    prompt_wiki_style,
    quick_repo_scan,
    should_offer_fast_mode,
)
from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION
from repowise.core.analysis.health.coverage import PARSERS as COVERAGE_PARSERS
from repowise.core.analysis.security_scan import SECURITY_SCANNER_VERSION
from repowise.core.docs_mode import docs_mode_state_fields, resolve_docs_mode
from repowise.core.generation.languages import SUPPORTED_LANGUAGES
from repowise.core.generation.styles import DEFAULT_STYLE, list_styles, resolve_style
from repowise.core.reasoning import REASONING_MODES
from repowise.core.repo_config import config_dependency_fingerprints

from ._interactive import offer_distill_rewrite_hook, offer_hook_install
from ._options import InitOptions, InitRun, resolve_structural_flags
from ._provider import announce_index_only, decision_key_in_env, resolve_docs_provider
from ._seed import delegate_to_seeded_update
from .generation import (
    concept_page_count,
    cost_gate_declined,
    estimate_generation,
    format_cost,
    page_type_label,
    run_repo_generation,
    structural_page_summary,
)
from .persistence import (
    apply_git_history_coverage_state,
    build_resume_controller,
    effective_run_mode_for_resume,
    git_tier_for_run_mode,
    persist_result,
    save_full_state_and_config,
)
from .reporting import show_analysis_summary, show_completion
from .workspace import _workspace_init


def _catch_up_savings(repo_path: Path) -> None:
    """Bank the savings this repository's agents were shown before indexing.

    Keyless, time-budgeted and best-effort, following the session-decision
    stage that runs here for the same reason: the history is already on disk,
    and a first index that ignores it shows an empty savings page for a
    repository that has been saving tokens for months.

    Not in ``repowise update``: update runs on every commit, and this reads a
    corpus bounded by how much the user has worked rather than by the repo.
    """
    try:
        from repowise.core.savings.transcript import sync_transcript_savings

        outcome = sync_transcript_savings(repo_path)
    except Exception:
        return
    if outcome.recorded:
        console.print(
            f"  [{OK}]✓[/] Recovered {outcome.saved_input_tokens:,} saved tokens "
            f"from agent history"
        )


def _record_init_outcome(
    *,
    result: Any,
    effective_index_only: bool,
    run_mode: str,
    provider: Any,
    embedder_name_resolved: str,
) -> None:
    """Attach an anonymous shape-of-the-index outcome to the ``command_run`` event.

    File and symbol buckets, run mode, top language and embedder on top of the
    shared index shape. Best-effort: never let telemetry break the command.
    """
    try:
        lang_dist = getattr(
            getattr(result, "repo_structure", None), "root_language_distribution", None
        )
        top_language = None
        if isinstance(lang_dist, dict) and lang_dist:
            top_language = max(lang_dist.items(), key=lambda kv: kv[1])[0]

        # docs_mode carries the same three values as the state field of that
        # name. It used to be a bool, which collapsed "template wiki" and "no
        # wiki" into one number, and template runs are the ones worth counting.
        pages = getattr(result, "generated_pages", None) or []
        if not effective_index_only and provider is not None:
            docs_mode = "llm"
        else:
            docs_mode = "deterministic" if pages else "none"
        buckets = {
            "file_count": getattr(result, "file_count", 0) or 0,
            "symbol_count": getattr(result, "symbol_count", 0) or 0,
        }
        if pages:
            buckets["deterministic_pages"] = sum(
                1 for p in pages if getattr(p, "provider_name", "") == "template"
            )
    except Exception:
        return
    record_index_outcome(
        index_only=effective_index_only,
        provider=provider,
        docs_mode=docs_mode,
        pages=pages,
        buckets=buckets,
        fields={
            "run_mode": run_mode,
            "top_language": top_language,
            "embedder": embedder_name_resolved or None,
        },
    )


def _print_structural_plan(
    *,
    result: Any,
    repo_path: Path,
    concurrency: int,
    language: str,
    onboarding: bool,
    wiki_style: str,
    max_file_pages: int | None,
    skip_tests: bool,
    skip_infra: bool,
) -> None:
    """The plan a dry run owes the deterministic branch.

    ``build_generation_plan`` is provider-free, so the same selector that
    generation would run gives the real per-type counts. A raw file count
    would not: ``select_pages`` caps file pages, and module, onboarding and
    infra pages are not in it at all.
    """
    from repowise.core.cost_estimator import build_generation_plan
    from repowise.core.generation import GenerationConfig

    gen_config = GenerationConfig.from_repo_config(
        load_config(repo_path),
        deterministic=True,
        max_concurrency=concurrency,
        language=language,
        enable_onboarding=onboarding,
        wiki_style=wiki_style,
        max_file_pages=max_file_pages,
    )
    kg_modules = getattr(getattr(result, "knowledge_graph_result", None), "modules", None) or None
    plans = build_generation_plan(
        result.parsed_files,
        result.graph_builder,
        gen_config,
        skip_tests,
        skip_infra,
        kg_modules=kg_modules,
    )

    table = Table(title="Generation Plan (dry run)", border_style=BRAND)
    table.add_column("Pages", style="cyan")
    table.add_column("Count", justify="right")
    total = 0
    for plan in plans:
        total += plan.count
        table.add_row(page_type_label(plan.page_type), f"{plan.count:,}")
    table.add_section()
    table.add_row("[bold]Total[/bold]", f"[bold]{total:,}[/bold]")
    console.print(table)
    console.print("  Dry run: every page above is rendered from structure. No wiki written.")


def _run_deterministic_generation_phase(
    *,
    repo_path: Path,
    result: Any,
    total_phases: int,
    concurrency: int,
    language: str,
    onboarding: bool,
    wiki_style: str,
    max_file_pages: int | None,
    embedder_name_resolved: str,
    embedder_was_requested: bool,
    resume: bool,
    timings: Any | None = None,
    warnings: list[str] | None = None,
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
    from repowise.cli.providers import template_run_embedder
    from repowise.core.generation import GenerationConfig
    from repowise.core.providers.llm.template import TemplateProvider

    embedder = template_run_embedder(embedder_name_resolved, embedder_was_requested)

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

    gen_config = GenerationConfig.from_repo_config(
        load_config(repo_path),
        deterministic=True,
        max_concurrency=concurrency,
        language=language,
        enable_onboarding=onboarding,
        wiki_style=wiki_style,
        max_file_pages=max_file_pages,
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
        timings=timings,
        warnings=warnings,
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
    wiki_style: str,
    max_file_pages: int | None,
    yes: bool,
    dry_run: bool,
    skip_tests: bool,
    skip_infra: bool,
    embedder_name_resolved: str,
    resume: bool,
    test_run: bool,
    timings: Any | None = None,
    warnings: list[str] | None = None,
) -> tuple[bool, bool]:
    """Run the LLM generation phase for a single-repo init.

    Returns ``(stop, cost_declined)``: ``stop`` is True when this was a dry run
    and the caller should return immediately; ``cost_declined`` is True when the
    user declined the cost gate (generation skipped, index still saved). Mutates
    ``result`` in place with the generated pages, vector store, and enriched KG.
    """
    from repowise.core.cost_estimator import STRUCTURAL_PAGE_TYPES
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
        wiki_style=wiki_style,
        max_file_pages=max_file_pages,
    )
    plans, est = estimate_generation(
        result=result,
        gen_config=gen_config,
        provider=provider,
        repo_path=repo_path,
        skip_tests=skip_tests,
        skip_infra=skip_infra,
    )

    # "Written by" replaces the old Level column (an internal generation-tier
    # integer nobody could act on) and answers the question the price below
    # raises: most of this total is free.
    table = Table(title="Generation Plan", border_style=BRAND)
    table.add_column("Pages", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Written by")
    model_pages = 0
    for plan in est.plans:
        from_model = plan.page_type not in STRUCTURAL_PAGE_TYPES
        model_pages += plan.count if from_model else 0
        table.add_row(
            page_type_label(plan.page_type),
            f"{plan.count:,}",
            "model" if from_model else "[dim]structure[/dim]",
        )
    table.add_section()
    table.add_row(
        "[bold]Total[/bold]",
        f"[bold]{est.total_pages:,}[/bold]",
        f"[bold]{model_pages:,} by model, {est.total_pages - model_pages:,} from structure[/bold]",
    )
    console.print(table)

    # Language breakdown
    lang_dist = result.repo_structure.root_language_distribution
    if lang_dist:
        lang_items = sorted(lang_dist.items(), key=lambda x: -x[1])[:6]
        lang_parts = [f"{lang} {pct:.0%}" for lang, pct in lang_items]
        console.print(f"  Languages: {', '.join(lang_parts)}")

    # Warn when a local provider runs with default concurrency
    from repowise.core.providers.llm.specs import PROVIDER_SPECS

    spec = PROVIDER_SPECS.get(provider.provider_name)
    if spec is not None and spec.local and concurrency > 4:
        warn(
            f"  {provider.provider_name} is a local provider "
            f"running with concurrency={concurrency}. "
            f"If you see timeout errors, try [bold]--concurrency 1[/bold]."
        )

    if onboarding:
        # Eight proper nouns in a wrapped dim block sat directly above the
        # price and competed with it; none of them is actionable here.
        console.print(
            "  [dim]Plus up to 8 curated onboarding pages (overview, architecture, "
            "getting started, and so on). Slots without enough signal are skipped.[/dim]"
        )
    else:
        console.print("  [dim]Onboarding collection: disabled (--no-onboarding).[/dim]")
    console.print()

    if dry_run:
        console.print("  Dry run: no pages generated.")
        return True, False

    # The single cost question. Every structural page is free; the spend is the
    # concept tree and the repo-wide synthesis pages, stated as one number.
    #
    # This gate declines rather than raises when it cannot ask (no terminal, over
    # the gate, no --yes): init's index is built but not yet persisted, so
    # raising here would discard the whole run. Declining hands control to the
    # caller's fallback, which renders the free structural wiki and saves the
    # index. `generate` and `update --full` raise instead, because their index
    # already exists and re-running with --yes costs nothing.
    concept_n = concept_page_count(plans)
    console.print(
        f"  Writing [bold]{concept_n:,}[/bold] subsystem pages with "
        f"[{VALUE}]{provider.model_name}[/]. Estimated [bold]{format_cost(est)}[/bold]."
    )
    structural = structural_page_summary(plans)
    if structural:
        console.print(f"  [dim]{structural}[/dim]")
    if cost_gate_declined(est, yes=yes, message="  Continue?"):
        console.print("  Skipping the model. [dim]Building the wiki from structure instead.[/dim]")
        console.print(
            "  [dim]Run [bold]repowise generate[/bold] any time to write the subsystem "
            "pages, or re-run init with [bold]--yes[/bold] to accept the cost now.\n"
            "  Post-commit updates stay index-only, so no hook will start a model run "
            "on its own.[/dim]"
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
        test_run=test_run,
        timings=timings,
        warnings=warnings,
    )
    return False, False


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


def _interactive_gate(
    *,
    isatty: bool,
    provider_name: str | None,
    index_only: bool,
    yes: bool,
    resume: bool,
) -> bool:
    """Whether the interactive questionnaire runs.

    Interactive requires a TTY, no explicit provider, docs on, and neither
    --yes nor --resume. --resume skips the gate because the prior run
    already answered every question and those answers are on disk
    (config.yaml); re-asking would let a resumed run diverge from the pages
    already written with them (issue #2098).
    """
    return isatty and provider_name is None and not index_only and not yes and not resume


def _run_workspace_init(opts: InitOptions, scan: Any) -> None:
    """Hand a directory holding several repos to the multi-repo flow."""
    _workspace_init(
        scan=scan,
        init_all=opts.init_all,
        exclude_patterns=list(opts.exclude),
        commit_limit=opts.commit_limit,
        follow_renames=opts.follow_renames,
        no_claude_md=opts.no_claude_md,
        agents_md=opts.agents_md,
        codex_setup=opts.codex_setup,
        distill_hook=opts.distill_hook,
        hook=opts.hook,
        editor_setup=opts.editor_setup,
        save_key=opts.save_key,
        include_submodules=opts.include_submodules,
        provider_name=opts.provider_name,
        model=opts.model,
        embedder_name=opts.embedder_name,
        index_only=opts.index_only,
        skip_tests=opts.skip_tests,
        skip_infra=opts.skip_infra,
        concurrency=opts.concurrency,
        reasoning=opts.reasoning,
        test_run=opts.test_run,
        yes=opts.yes,
        dry_run=opts.dry_run,
        resume=opts.resume,
        force=opts.force,
        onboarding=opts.onboarding,
        # Apply the chosen style uniformly across the workspace's repos
        # (no per-repo interactive prompt in the multi-repo flow).
        wiki_style=resolve_style(opts.wiki_style).name,
        language=opts.language_opt,
        run_mode=opts.run_mode,
    )


def _start_run(opts: InitOptions, repo_path: Path, start: float) -> InitRun:
    """Prepare ``.repowise/``, logging and the resume mode, then open the run."""
    ensure_repowise_dir(repo_path)
    load_dotenv(repo_path)

    # Quiet library/structlog output so the progress bars are the only output;
    # `-v` lets repowise's debug lines through for troubleshooting.
    configure_cli_logging(verbose=opts.verbose)

    # On --resume, continue the prior run's git tier so a resumed fast index
    # doesn't silently fall back to the expensive FULL tier (issue #341). Done
    # before the interactive gate so a resume never re-prompts for the mode.
    run_mode = effective_run_mode_for_resume(repo_path, opts.run_mode, opts.resume)
    opts = replace(opts, run_mode=run_mode, index_only=opts.index_only or run_mode == "fast")

    # --yes forces non-interactive even on a TTY (mirrors the workspace path),
    # so a scripted `init -y` never blocks on the mode-selection menu.
    is_interactive = _interactive_gate(
        isatty=sys.stdin.isatty(),
        provider_name=opts.provider_name,
        index_only=opts.index_only,
        yes=opts.yes,
        resume=opts.resume,
    )

    if opts.resume:
        console.print(
            f"[bold]Resuming[/] the previous run in {repo_path}, "
            "reusing the answers it already saved to config.yaml."
        )
    return InitRun(
        opts=opts,
        repo_path=repo_path,
        start=start,
        is_interactive=is_interactive,
        generate_docs=not opts.index_only,
    )


def _ask_mode(run: InitRun) -> None:
    """The mode menu, mapped onto the two axes (docs on/off, customize yes/no).

    ``sys.stdin.isatty()`` is not a reliable answer to "can I read from
    stdin". On Windows under Git Bash, ``repowise init < /dev/null`` reports a
    TTY and then reads EOF on the first question; the same goes for some pty
    wrappers and ``docker run -t`` without -i. Agents drive init through
    exactly those shapes, so the first prompt is the probe: if it cannot be
    answered, drop to the non-interactive path rather than dying on a question
    nobody can hear. Only EOFError is caught. A real Ctrl-C raises
    KeyboardInterrupt/Abort and must still stop the run.
    """
    try:
        mode = interactive_mode_select(console)
    except EOFError:
        run.is_interactive = False
        console.print(
            f"\n[{WARN}]No answer available on stdin[/] "
            "[dim]- continuing with defaults. Pass --yes to skip the "
            "questions, or --prose / --no-prose to choose directly.[/dim]"
        )
        return

    #   full       -> docs on,  optional customize
    #   index_only -> docs off, optional customize
    #   advanced   -> docs toggle, always customize
    if mode == "index_only":
        run.generate_docs = False
        run.customize = interactive_customize_offer(console, generate_docs=False)
    elif mode == "advanced":
        run.generate_docs = interactive_generate_docs_toggle(console)
        run.customize = True
    elif mode is not None:  # full
        run.generate_docs = True
        run.customize = interactive_customize_offer(console, generate_docs=True)
    if mode is not None:
        run.opts = replace(run.opts, index_only=not run.generate_docs)


def _ask_provider(run: InitRun) -> None:
    """Provider selection, only when docs will be generated.

    Index-only runs auto-detect a decision-extraction provider later without
    prompting.
    """
    opts = run.opts
    selection = interactive_provider_config_select(
        console,
        opts.model,
        opts.reasoning,
        repo_path=run.repo_path,
        save_key=opts.save_key,
        prefer=agent_providers_set_up(run.repo_path),
    )
    run.opts = replace(
        opts,
        provider_name=selection.provider_name,
        model=selection.model,
        reasoning=selection.reasoning,
    )


def _apply_advanced_answers(run: InitRun, adv: dict[str, Any]) -> None:
    """Fold the advanced-config answers into the run."""
    opts = run.opts
    # Indexing knobs (always present).
    changes: dict[str, Any] = {
        "commit_limit": adv["commit_limit"],
        "follow_renames": adv["follow_renames"],
        "skip_tests": adv["skip_tests"],
        "skip_infra": adv["skip_infra"],
        "exclude": adv["exclude"],
        "include_submodules": adv.get("include_submodules", opts.include_submodules),
        "run_mode": adv.get("run_mode", opts.run_mode),
        # Asked in both branches: an index-only run renders a wiki too, and
        # those pages embed like any other, so the answer applies either way.
        "embedder_name": adv.get("embedder") or opts.embedder_name,
    }
    # Asked in both branches for the same reason: an index-only run renders
    # file pages too.
    run.max_file_pages_choice = adv.get("max_file_pages")
    # Generation knobs (only gathered when docs are on).
    if run.generate_docs:
        changes["concurrency"] = adv["concurrency"]
        changes["reasoning"] = adv.get("reasoning") or opts.reasoning
        changes["test_run"] = adv["test_run"]
        changes["onboarding"] = adv.get("onboarding", opts.onboarding)
        if adv.get("wiki_style"):
            changes["wiki_style"] = adv["wiki_style"]
        if adv.get("language"):
            run.language_choice = adv["language"]
    # Fast mode (picked in the indexing section) is a no-LLM index, so it
    # forces index-only even if the user had asked for docs.
    if changes["run_mode"] == "fast":
        changes["index_only"] = True
        run.generate_docs = False
    run.opts = replace(opts, **changes)


def _offer_fast_mode(run: InitRun, scan_info: Any) -> None:
    """No customization: still offer the fast first-index on large repos.

    Default yes for an index-only run (docs already opted out), no for a full
    run (the user explicitly asked for docs).
    """
    if (
        run.opts.run_mode != "fast"
        and should_offer_fast_mode(scan_info)
        and interactive_fast_mode_offer(console, scan_info, default_fast=not run.generate_docs)
    ):
        run.opts = replace(run.opts, run_mode="fast", index_only=True)
        run.generate_docs = False


def _ask_questions(run: InitRun) -> None:
    """The interactive questionnaire (TTY, no explicit flags)."""
    print_banner(console, repo_name=run.repo_path.name)
    with console.status("  Scanning repository…", spinner=OWL_SPINNER):
        scan_info = quick_repo_scan(run.repo_path)
    print_scan_summary(console, scan_info)

    _ask_mode(run)
    # ``is_interactive`` can have been cleared by an unanswerable menu, in
    # which case every remaining question here is unanswerable too.
    if run.generate_docs and run.is_interactive:
        _ask_provider(run)

    if run.customize:
        adv = interactive_advanced_config(
            console,
            scan=scan_info,
            allow_fast=True,
            prompt_reasoning=False,
            generate_docs=run.generate_docs,
            wiki_style=run.opts.wiki_style,
            language=run.opts.language_opt,
        )
        _apply_advanced_answers(run, adv)
    elif run.is_interactive:
        _offer_fast_mode(run, scan_info)

    # Wiki style: prompt for a docs run when not already chosen (via the
    # --wiki-style flag or the advanced generation section). Index-only runs
    # generate no pages, so the question is skipped. --yes uses the default.
    if (
        run.generate_docs
        and run.opts.wiki_style is None
        and not run.opts.yes
        and run.is_interactive
    ):
        run.opts = replace(run.opts, wiki_style=prompt_wiki_style(console))


def _editor_options(run: InitRun) -> Any:
    """Which editor files and integrations this run writes."""
    opts = run.opts
    editor_options = resolve_editor_setup_options(
        disabled_project_files=get_default_disabled_project_files(
            no_claude_md=opts.no_claude_md,
        ),
        project_file_overrides=get_default_project_file_overrides(
            agents_md=opts.agents_md,
        ),
        integration_overrides=get_default_integration_overrides(
            codex_setup=opts.codex_setup,
        ),
    )
    # Ask which agents to wire up whenever the user is engaging interactively:
    # either generating docs or customizing an index-only run. Not asked when
    # editor setup is off: every answer would be discarded by
    # `write_editor_project_files`.
    if opts.editor_setup and run.is_interactive and (run.generate_docs or run.customize):
        editor_options = select_agents_interactively(console, run.repo_path, editor_options)
    return editor_options


def _resolve_settings(run: InitRun) -> None:
    """Resolve each setting from its flag, the interactive answer and config.yaml."""
    opts = run.opts
    config = load_config(run.repo_path)
    run.config = config
    # Output language: CLI flag > advanced-mode choice > config.yaml > English.
    run.language = opts.language_opt or run.language_choice or config.get("language", "en")
    # File-page cap: flag > advanced-mode answer > config.yaml > unset (policy).
    file_pages = opts.max_file_pages_opt
    if file_pages is None:
        file_pages = run.max_file_pages_choice
    run.max_file_pages = resolve_max_file_pages(file_pages, config)
    run.resolved_reasoning = resolve_reasoning(opts.reasoning, config)
    # Merge exclude_patterns from config.yaml and --exclude/-x flags.
    run.exclude_patterns = list(config.get("exclude_patterns") or []) + list(opts.exclude)

    # Commit limit: CLI flag → config.yaml → default.
    from repowise.core.ingestion.git_indexer._constants import _DEFAULT_COMMIT_LIMIT

    commit_limit = opts.commit_limit or config.get("commit_limit") or _DEFAULT_COMMIT_LIMIT
    run.resolved_commit_limit = max(1, min(commit_limit, 10000))
    if opts.commit_limit is not None:
        config["commit_limit"] = run.resolved_commit_limit

    # follow_renames: CLI flag → config.yaml.
    run.resolved_follow_renames = opts.follow_renames or config.get("follow_renames", False)
    if opts.follow_renames:
        config["follow_renames"] = True

    run.embedder_name_resolved = resolve_embedder(opts.embedder_name)
    run.embedder_was_requested = _embedder_was_requested(opts.embedder_name, config.get("embedder"))


def _confirm_wiki_overwrite(run: InitRun) -> bool:
    """Read the prior state; False when the user keeps a model-written wiki.

    A template wiki overwrites a model-written one page for page: the upsert
    replaces content and stamps provider_name="template", keeping the old text
    only as a version snapshot. Fine when the user meant it, a bad surprise
    when they were reaching for a re-index, so ask. Non-interactive runs refuse
    rather than guess. ``--yes`` and ``--force`` both mean "do not ask me".
    """
    prior_state = load_state(run.repo_path)
    # Whether this repo has ever been indexed, captured before the run writes
    # its own state. A first index tells the user to restart Claude Code to
    # load the tools; a re-index does not.
    run.first_index = not prior_state.get("last_sync_commit")
    run.prior_docs_mode = resolve_docs_mode(prior_state)
    opts = run.opts
    if not opts.index_only or run.prior_docs_mode != "llm" or opts.force or opts.yes:
        return True
    console.print(
        f"\n[{WARN}]This repo already has a model-written wiki.[/] "
        "Indexing without a model\nrewrites every page from templates; the "
        "written versions stay in page history."
    )
    if not sys.stdin.isatty():
        raise reasoned_error(
            "Refusing to replace a model-written wiki with template pages. "
            "Re-run with --yes to confirm, or drop --index-only.",
            reason="wiki_overwrite_unconfirmed",
        )
    if not click.confirm("  Replace the written wiki with template pages?", default=False):
        console.print("[dim]Nothing changed.[/dim]")
        return False
    return True


def _resolve_decision_provider(run: InitRun) -> None:
    """Index-only: a decision-extraction provider when one is named or keyed."""
    opts = run.opts
    try:
        if opts.provider_name or decision_key_in_env():
            run.decision_provider = resolve_provider(opts.provider_name, opts.model, run.repo_path)
    except Exception:
        pass


def _resolve_provider_stage(run: InitRun) -> None:
    """Which model this run calls: none, a decision extractor, or the docs model."""
    if run.opts.index_only:
        _resolve_decision_provider(run)
        announce_index_only(run)
    else:
        resolve_docs_provider(run)


def _prepare_single_repo(opts: InitOptions, repo_path: Path, start: float) -> InitRun | None:
    """Everything a single-repo run settles before indexing. None to stop."""
    run = _start_run(opts, repo_path, start)
    if run.is_interactive:
        _ask_questions(run)

    # Resolve the effective style (CLI flag > interactive prompt > default) and
    # canonicalize it. Used by generation and persisted so update/restyle honor it.
    run.opts = replace(run.opts, wiki_style=resolve_style(run.opts.wiki_style).name)
    run.editor_options = _editor_options(run)
    _resolve_settings(run)
    if not _confirm_wiki_overwrite(run):
        return None
    _resolve_provider_stage(run)
    return run


@click.command("init")
@click.argument("path", required=False, default=None)
@click.option(
    "--provider",
    "provider_name",
    default=None,
    help=(
        "LLM provider name (anthropic, openai, openrouter, gemini, "
        "deepseek, kimi, ollama, litellm, codex_cli, claude_cli, opencode, "
        "edenai, mock). "
        "In a terminal, a missing key is prompted for; openai also asks for "
        "an optional OpenAI-compatible Base URL."
    ),
)
@click.option("--model", default=None, help="Model identifier override.")
@click.option(
    "--embedder",
    "embedder_name",
    default=None,
    type=click.Choice(["gemini", "openai", "openrouter", "ollama", "edenai", "mock"]),
    help=(
        "Embedder for RAG: gemini | openai | openrouter | ollama | edenai | mock "
        "(default: auto-detect)."
    ),
)
@click.option("--skip-tests", is_flag=True, default=False, help="Skip test files.")
@click.option("--skip-infra", is_flag=True, default=False, help="Skip infrastructure files.")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    # "No wiki", not "writes nothing": the pipeline still warms its parse and
    # duplication caches, which are derived data.
    help="Show the generation plan and cost estimate. Writes no wiki.",
)
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip cost confirmation prompt.")
@click.option(
    "--resume",
    is_flag=True,
    default=False,
    help=(
        "Skip pages already generated in the vector store and continue from "
        "where a previous run stopped. Safe no-op on a fully indexed repo."
    ),
)
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
    "--prose/--no-prose",
    "prose",
    default=None,
    help=(
        "Write the subsystem (concept) pages as model prose (needs a key), or "
        "render the whole wiki from structure with no model and no spend. Every "
        "other page is structural either way. Default: prose when a key is "
        "available. With --no-prose, an explicit --provider or API key still "
        "enables model-based decision extraction. Use --mode fast for no wiki at all."
    ),
)
@click.option(
    "--index-only",
    is_flag=True,
    default=False,
    hidden=True,
    help="Deprecated alias for --no-prose.",
)
@click.option(
    "--docs",
    "docs_opt",
    type=click.Choice(["llm", "deterministic"]),
    default=None,
    hidden=True,
    help="Deprecated: use --prose / --no-prose. 'llm' == --prose, 'deterministic' == --no-prose.",
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
    help="Max commits to analyze per file and for co-change detection (default: 5000, max: 10000). Saved to config.",
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
        "Let repowise's hooks compact what your agent sees. Noisy commands "
        "(tests, builds, git, searches) run through `repowise distill`; a "
        "whole-file Read comes back as a skeleton (`hooks.read_skeleton`); a "
        "grep matching many files comes back as a per-file digest "
        "(`hooks.search_digest`); re-reading an unchanged, unedited file comes "
        "back as a pointer to the earlier read (`hooks.read_reread`). One "
        "consent, and this flag decides all four without a prompt. Default: "
        "ask when interactive, skip otherwise. In workspace mode the verdict "
        "applies to every selected repo."
    ),
)
@click.option(
    "--hook/--no-hook",
    "hook",
    default=None,
    help=(
        "Install the post-commit hook that runs `repowise update` after each "
        "commit, so the index stays current without anyone typing it. Default: "
        "on. Interactive runs ask; --yes and non-interactive runs install it and "
        "print how to undo it (`repowise hook uninstall`). --no-hook skips it. "
        "--no-editor-setup skips it too, since a git hook is a write outside "
        ".repowise/. In workspace mode the choice applies to every selected repo."
    ),
)
@click.option(
    "--editor-setup/--no-editor-setup",
    "editor_setup",
    default=True,
    help=(
        "Wire repowise into your editors. Covers both halves: the machine-wide "
        "config (the Claude Code / Claude Desktop MCP entry, the Claude Code "
        "hooks, the distill rewrite-hook offer) and the project-local files "
        "(.mcp.json, .claude/CLAUDE.md, .vscode/mcp.json, "
        ".vscode/extensions.json). Default: on. Use --no-editor-setup to index "
        "a repo without writing anything into it or outside it; only "
        ".repowise/ is touched, and 'repowise mcp .' still prints the config "
        "to connect a client by hand. REPOWISE_SKIP_EDITOR_SETUP=1 does the "
        "same thing for CI and sandboxes, and wins: with it set, --editor-setup "
        "does not turn setup back on."
    ),
)
@click.option(
    "--save-key/--no-save-key",
    "save_key",
    default=True,
    help=(
        "Save the provider API key this run authenticated with into "
        ".repowise/.env (gitignored, owner-only). Default: on, because a scripted "
        "init that succeeds must leave a repo whose MCP server can answer, "
        "and the key would otherwise be lost with the shell that set it. Use "
        "--no-save-key when the key is injected per-process (CI secrets, a "
        "shared machine) and must not be written to disk. "
        "REPOWISE_NO_SAVE_KEY=1 does the same for CI and sandboxes."
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
    "--max-file-pages",
    "max_file_pages_opt",
    type=int,
    default=None,
    metavar="N",
    help=(
        "Most file pages to emit, highest importance first. Omit to let the size "
        "policy decide: untouched on a normal repo, held to a ceiling on a very "
        "large one. Pass 0 for one page per eligible file however many that is. "
        "Saved to config.yaml so later runs keep it."
    ),
)
@click.option(
    "--coverage-report",
    "coverage_report",
    type=click.Path(exists=True, dir_okay=False),
    multiple=True,
    metavar="PATH",
    help=(
        f"Test-coverage report(s) to ingest ({' / '.join(COVERAGE_PARSERS)}). "
        "Repeatable. When omitted, common locations (coverage/lcov.info, "
        "**/cobertura.xml, ...) are auto-discovered. This is test coverage for "
        "code-health, not a documentation-breadth knob."
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
def init_command(**params: Any) -> None:
    """Generate wiki documentation for a codebase.

    PATH defaults to the current directory.
    Use --no-prose to render the whole wiki from structure with no model and no
    key; --prose writes the subsystem pages with a model. Every other page is
    structural either way.
    Use --mode fast for a quick graph + essential-git index of a very large repo.
    """
    opts = resolve_structural_flags(InitOptions(**params))
    start = time.monotonic()
    repo_path = resolve_repo_path(opts.path)

    if not repo_path.is_dir():
        raise reasoned_error(f"Not a directory: {repo_path}", reason="invalid_path")

    # ---- Workspace detection ----
    # If the path contains multiple git repos (and is not itself a single repo),
    # branch into the multi-repo workspace flow.  --no-workspace bypasses this
    # entirely and forces single-repo mode regardless of what scan finds.
    from repowise.core.workspace import scan_for_repos

    scan = scan_for_repos(repo_path, include_submodules=opts.include_submodules)

    if delegate_to_seeded_update(opts, repo_path, scan):
        return
    if len(scan.repos) > 1 and not opts.no_workspace:
        _run_workspace_init(opts, scan)
        return

    # If a single repo was found inside the given directory (not at root),
    # redirect to it so the user doesn't have to specify the exact path.
    if len(scan.repos) == 1 and scan.repos[0].path != repo_path:
        repo_path = scan.repos[0].path

    run = _prepare_single_repo(opts, repo_path, start)
    if run is None:
        return
    opts = run.opts
    index_only = opts.index_only
    run_mode = opts.run_mode
    dry_run = opts.dry_run
    force = opts.force
    yes = opts.yes
    resume = opts.resume
    skip_tests = opts.skip_tests
    skip_infra = opts.skip_infra
    include_submodules = opts.include_submodules
    concurrency = opts.concurrency
    test_run = opts.test_run
    coverage_report = opts.coverage_report
    onboarding = opts.onboarding
    wiki_style = opts.wiki_style
    commit_limit = opts.commit_limit
    editor_setup = opts.editor_setup
    save_key = opts.save_key
    hook = opts.hook
    distill_hook = opts.distill_hook
    editor_options = run.editor_options
    config = run.config
    language = run.language
    max_file_pages = run.max_file_pages
    resolved_reasoning = run.resolved_reasoning
    exclude_patterns = run.exclude_patterns
    resolved_commit_limit = run.resolved_commit_limit
    resolved_follow_renames = run.resolved_follow_renames
    embedder_name_resolved = run.embedder_name_resolved
    embedder_was_requested = run.embedder_was_requested
    _first_index = run.first_index
    _prior_docs_mode = run.prior_docs_mode
    provider = run.provider
    decision_provider = run.decision_provider
    no_provider = run.no_provider

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
    # None on a dry run: decision extraction and KG enrichment both call this
    # client before the generation phase's dry-run return is reached.
    llm_client = None if dry_run else (provider if not index_only else decision_provider)

    from repowise.core.pipeline import PhaseTimingRecorder, run_pipeline
    from repowise.core.pipeline.modes import OrchestratorMode

    orchestrator_mode = OrchestratorMode.FAST if run_mode == "fast" else OrchestratorMode.STANDARD

    # The store generation would build anyway, hoisted so the analysis
    # checkpoint inside the pipeline can dedup decisions against it;
    # ``run_repo_generation`` reuses this object. Never more than that store,
    # so the exclusions are the runs that build none: a dry run, and index-only
    # fast mode, which also pins no embedder for a table to be read back with.
    # Keyless is dropped rather than handed to a matcher that refuses it.
    index_vector_store = None
    if not dry_run and not (index_only and run_mode == "fast"):
        from repowise.cli.providers import build_embedder, build_vector_store, template_run_embedder
        from repowise.core.providers.embedding import store_has_semantic_vectors

        _store_embedder = (
            template_run_embedder(embedder_name_resolved, embedder_was_requested)
            if index_only or no_provider
            else embedder_name_resolved
        )
        _candidate = build_vector_store(repo_path, build_embedder(_store_embedder, repo_path))
        index_vector_store = _candidate if store_has_semantic_vectors(_candidate) else None

    index_columns: list[Any] = [
        SpinnerColumn(spinner_name=OWL_SPINNER, style=BRAND_STYLE),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MaybeCountColumn(),
        TimeElapsedColumn(),
    ]
    # Only when a model can actually be called. On a --no-prose run the column
    # would sit at $0.000 for the whole multi-minute index, which reads as an
    # unanswered question rather than an answer (same call generation.py makes
    # for the generation bar).
    if llm_client is not None:
        index_columns.append(TextColumn("[" + OK + "]${task.fields[cost]:.3f}[/]"))

    with Progress(*index_columns, console=console) as progress_bar:
        rich_callback = RichProgressCallback(progress_bar, console, total_phases=total_phases)
        # Wrap the Rich callback so we can record per-phase wall-clock
        # durations without changing the pipeline API. Timings get
        # persisted to state.json below.
        callback = PhaseTimingRecorder(rich_callback)
        # The denominator. Phases nest (``persist.fts`` inside ``persist``)
        # and some overlap, so the totals can exceed it - without it there is
        # no way to tell an unrecorded stage from an overlapping one.
        callback.table.start("run")

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
                    vector_store=index_vector_store,
                    concurrency=concurrency,
                    test_run=test_run,
                    mode=orchestrator_mode,
                    progress=callback,
                    existing_kg_fingerprint=_prev_kg_fp,
                    resume_controller=controller,
                    coverage_report_paths=(
                        [Path(p) for p in coverage_report] if coverage_report else None
                    ),
                    # Local `init` is the one place a fact about this machine's
                    # environment is a fact about the user's environment.
                    derive_environment_facts=True,
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
                f"\n{mini(EYES_SLEEPY)} [{WARN}]Interrupted.[/] Indexed work so far has been "
                "saved — run [bold]repowise init --resume[/] to continue where it stopped."
            )
            from repowise.cli.hints import maybe_hint

            maybe_hint("interrupt")
            return

    # What the run degraded on, in a place an agent can read after the
    # terminal is gone. Timings are read after persistence instead, so
    # generation and the persist tail are in the table too.
    run_warnings: list[str] = list(rich_callback.warnings)

    # ---- Analysis summary (shown between analysis and generation) ----
    show_analysis_summary(result)

    # ---- Phase 3: Generation ----
    # The priced branch below has its own dry-run return; these two rendered
    # the template wiki and fell through to persistence, overwriting a
    # model-written one. Same shape as #2005 (update) and #1526 (workspace).
    if dry_run and (index_only or no_provider):
        if run_mode == "fast":
            # Fast skips generation outright, so promising a wiki here would
            # preview the opposite of what the real run does.
            console.print("\n  Dry run: fast mode indexes graph and git only. No wiki written.")
        else:
            _print_structural_plan(
                result=result,
                repo_path=repo_path,
                concurrency=concurrency,
                language=language,
                onboarding=onboarding,
                wiki_style=wiki_style,
                max_file_pages=max_file_pages,
                skip_tests=skip_tests,
                skip_infra=skip_infra,
            )
        return

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
            max_file_pages=max_file_pages,
            embedder_name_resolved=embedder_name_resolved,
            embedder_was_requested=embedder_was_requested,
            resume=resume,
            timings=callback.table,
            warnings=run_warnings,
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
            wiki_style=wiki_style,
            max_file_pages=max_file_pages,
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
            warnings=run_warnings,
            test_run=test_run,
            timings=callback.table,
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
                max_file_pages=max_file_pages,
                # The user has a provider and just declined a bill, not the
                # embedder they configured, so honour it either way.
                embedder_was_requested=True,
                embedder_name_resolved=embedder_name_resolved,
                resume=resume,
                timings=callback.table,
                warnings=run_warnings,
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

    # A bar rather than the old indeterminate spinner: full-text indexing walks
    # every generated page one await at a time, so on a few thousand pages this
    # stage ran for minutes with nothing on screen but a spinner, immediately
    # after a generation bar that had just claimed to be finished. The columns
    # are the index run's, minus the cost one — nothing here spends tokens.
    with Progress(
        SpinnerColumn(spinner_name=OWL_SPINNER, style=BRAND_STYLE),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MaybeCountColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as persist_bar:
        persist_callback = callback.rebind(RichProgressCallback(persist_bar, console))
        # Indeterminate: persistence has no page-proportional loop to count
        # since the full-text index became a single statement. Announced here
        # so the stage is never silent.
        persist_callback.on_phase_start("persist", None)
        run_async(persist_result(result, repo_path, persist_callback, callback.table))
        persist_callback.on_phase_done("persist")
    # Persistence has its own callback, so its warnings need folding into the
    # run record explicitly — the state write below is the last chance.
    run_warnings.extend(persist_callback.warnings)
    # Read now, not before generation: ingestion, analysis, generation and
    # persistence all write into the one table.
    callback.table.stop("run")
    phase_timings: dict[str, float] = callback.timings
    console.print(f"  [{OK}]✓[/] Database updated")

    _catch_up_savings(repo_path)

    # Persist the onboarding choice so subsequent `repowise update` runs
    # honor it without re-passing the flag. Default True is omitted to keep
    # config files tidy — only the override is recorded.
    if not onboarding:
        save_config_partial(repo_path, enable_onboarding=False)

    # Persist the file-page decision so `repowise update --full` and `repowise
    # generate` keep the wiki the size that was asked for instead of re-deciding.
    # Recorded whether or not a model wrote the pages, since the cap applies to the
    # template wiki just the same. A refusal (0) is recorded like any other answer:
    # leaving it unrecorded would hand the next run back to the size policy the
    # user just declined. Only an unset value stays unwritten.
    if max_file_pages is not None and max_file_pages != config.get("max_file_pages"):
        # save_config_partial drops None values, and 0 is not None, so an explicit
        # refusal reaches the file.
        save_config_partial(repo_path, max_file_pages=max_file_pages)

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
    # One flag, one meaning: --no-editor-setup now suppresses the project-local
    # writes as well as the global registration. The paths come back so the
    # completion panel can name what landed in the working tree.
    # Render after state persistence below so generated guidance reads the same
    # canonical index_scope that status/API/MCP expose.
    files_written: list[Path] = []
    register_editor_clients(console, repo_path, no_editor_setup=not editor_setup)

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
    apply_git_history_coverage_state(base_state, result)
    from repowise.cli.providers import semantic_search_status
    from repowise.core.generation.selection import count_documentable_files
    from repowise.core.index_scope import dropped_files_scope, file_page_scope, stamp_index_scope

    _scope_embedder = embedder_name_resolved if not effective_index_only else _index_only_embedder
    _unavailable = []
    if getattr(result, "health_report", None) is None:
        _unavailable.append("health")
    _skipped = ["generation"] if run_mode == "fast" else []
    stamp_index_scope(
        base_state,
        {"commit_limit": resolved_commit_limit, "max_file_pages": max_file_pages},
        run_mode=run_mode,
        content_provenance={"none": "none", "deterministic": "template", "llm": "model"}[
            _docs_mode
        ],
        git_tier=git_tier_for_run_mode(run_mode),
        git_commit_cap=resolved_commit_limit,
        dropped_files=dropped_files_scope(getattr(result, "traversal_stats", None)),
        file_pages={
            "configured_cap": max_file_pages,
            **(
                result.generation_scope
                if getattr(result, "generation_scope", None)
                else file_page_scope(
                    configured_cap=max_file_pages,
                    eligible=count_documentable_files(result.parsed_files),
                    generated_pages=result.generated_pages,
                )
            ),
        },
        analysis={"unavailable": _unavailable, "skipped": _skipped},
        provider={
            "name": provider.provider_name if provider is not None else None,
            "model": provider.model_name if provider is not None else None,
            "embedder": _scope_embedder,
            "reused": False,
            "model_cost_possible": _docs_mode == "llm",
        },
        search={
            "full_text": "available" if result.generated_pages else "unavailable",
            "semantic": (
                semantic_search_status(
                    _scope_embedder, getattr(result, "embed_failed_pages", 0)
                )
                if result.generated_pages
                else "unavailable"
            ),
            "next_command": "repowise reindex" if result.generated_pages else None,
        },
        upgrade={
            "status": "pending" if _docs_mode != "llm" else "not_applicable",
            "retryable": _docs_mode != "llm",
            "completed_stages": [],
            "next_stage": "git_backfill" if run_mode == "fast" else "generation",
        },
    )
    # Record whether submodules were indexed so `repowise update` rebuilds
    # the graph with the same boundary semantics (same pattern as git_tier:
    # missing → False keeps legacy behavior for old state files).
    base_state["include_submodules"] = include_submodules
    if phase_timings:
        base_state["phase_timings"] = phase_timings
    # Cleared before it is set: ``base_state`` came from the previous run's
    # state.json, and a fixed re-run must not inherit its predecessor's
    # degradation report (see save_full_state_and_config for the same rule).
    base_state.pop("degraded", None)
    if run_warnings:
        base_state["degraded"] = run_warnings
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
        base_state["config_dependency_fingerprints"] = config_dependency_fingerprints(repo_path)
        # Index-only is the keyless default, so this is where most installs get
        # their stamp. Without it `health_analyzer_changed` reads absent-as-
        # unchanged and the version trigger never fires for them.
        base_state["health_analyzer_version"] = HEALTH_ANALYZER_VERSION
        # Same reasoning for the security scanner (#3072): this run just
        # scanned every file, so start tracking its version here too.
        base_state["security_scanner_version"] = SECURITY_SCANNER_VERSION
        # The pages were just rendered by this release's structural renderer.
        # Unstamped, the first update would read the index as pre-stamp and
        # re-render the whole wiki it had only just written.
        from repowise.cli.commands.update_cmd.command import _current_renderer_fingerprint

        base_state["renderer_fingerprint"] = _current_renderer_fingerprint(repo_path)
        # This run just scored every file, so the periodic re-score cadence
        # starts now. Without the stamp the gate reads "never re-scored" and the
        # very next update re-scores the whole repo init had only just scored.
        #
        # Only when there really is a report. The health phase swallows its own
        # failures and returns None, and nothing is persisted for a None report,
        # so stamping there would suppress the first update's re-score - the
        # only thing that would have repopulated the missing rows. Same rule the
        # two update-side writers follow: they stamp only on a re-score that
        # returned True. None (no git) is left unstamped too: the gate cannot
        # fire without a head_ts either, so there is nothing to suppress.
        _head_ts = head_commit_ts(repo_path) if getattr(result, "health_report", None) else None
        if _head_ts is not None:
            base_state["last_full_rescore_at"] = _head_ts
        # Index-only still renders the full concept tree (deterministically, from
        # templates), so the store has the current capability — stamp the terminal
        # version rather than clamping it below the reindex gate and falsely
        # recommending a re-index of a store this run just built.
        save_state(repo_path, base_state, full_index=True)

    # ---- State + config (full mode only) ----
    if not effective_index_only and provider:
        save_full_state_and_config(
            repo_path=repo_path,
            result=result,
            provider=provider,
            phase_timings=phase_timings,
            degraded=run_warnings,
            embedder_name_resolved=embedder_name_resolved,
            exclude_patterns=exclude_patterns,
            commit_limit=commit_limit,
            resolved_commit_limit=resolved_commit_limit,
            resolved_reasoning=resolved_reasoning,
            max_file_pages=max_file_pages,
            include_submodules=include_submodules,
            save_key=save_key,
        )

    # Every store is final: store the views ``repowise next`` and the
    # dashboard read, before the editor files quote Fix first from them.
    refresh_read_snapshots(repo_path)
    files_written = write_editor_project_files(
        console,
        repo_path,
        options=editor_options,
        no_editor_setup=not editor_setup,
    )
    if editor_setup:
        # The index may carry a coverage ingest now; see ``sync_repo_hook``.
        from repowise.cli.commands.augment_cmd.coverage_reingest import sync_repo_hook

        sync_repo_hook(repo_path, console)

    _record_init_outcome(
        result=result,
        effective_index_only=effective_index_only,
        run_mode=run_mode,
        provider=provider,
        embedder_name_resolved=embedder_name_resolved,
    )

    # Post-commit auto-sync hook (both index-only and full modes)
    offer_hook_install(
        console,
        [repo_path],
        flag=hook,
        yes=yes,
        no_editor_setup=not editor_setup,
    )

    # Opt-in distill command-rewrite hook for Claude Code. The workspace flow
    # runs its own offer across all selected repos inside _workspace_init.
    offer_distill_rewrite_hook(
        console,
        [repo_path],
        distill_hook,
        yes=yes,
        no_editor_setup=not editor_setup,
    )

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
        no_editor_setup=not editor_setup,
    )
    show_completion(
        repo_path=repo_path,
        result=result,
        start=start,
        effective_index_only=effective_index_only,
        run_mode=run_mode,
        provider=provider,
        setup=_setup_outcome,
        files_written=files_written,
    )
    # Raised last, so everything above is kept: pages, state and full-text
    # search are fine. Exiting 0 here is what let a scripted run record a
    # healthy semantic index that held no vectors.
    from repowise.cli.providers import embed_failure_message

    _embed_error = embed_failure_message(
        _scope_embedder, getattr(result, "embed_failed_pages", 0)
    )
    if _embed_error:
        raise click.ClickException(_embed_error)
