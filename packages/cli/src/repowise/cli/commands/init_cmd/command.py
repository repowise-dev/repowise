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
from rich.table import Table

from repowise.cli._setup import configure_cli_logging
from repowise.cli.editor_integrations.defaults import (
    get_default_disabled_project_files,
    get_default_integration_overrides,
    get_default_project_file_overrides,
)
from repowise.cli.editor_setup import (
    resolve_editor_setup_options,
    select_agents_interactively,
)
from repowise.cli.errors import reasoned_error
from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    load_config,
    load_state,
    resolve_max_file_pages,
    resolve_provider,
    resolve_reasoning,
    resolve_repo_path,
    warn,
)
from repowise.cli.providers import resolve_embedder
from repowise.cli.providers.embedders import embedder_was_requested as _embedder_was_requested
from repowise.cli.ui import (
    BRAND,
    OWL_SPINNER,
    VALUE,
    WARN,
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
from repowise.core.analysis.health.coverage import PARSERS as COVERAGE_PARSERS
from repowise.core.docs_mode import resolve_docs_mode
from repowise.core.generation.languages import SUPPORTED_LANGUAGES
from repowise.core.generation.styles import DEFAULT_STYLE, list_styles, resolve_style
from repowise.core.reasoning import REASONING_MODES

from ._finish import (
    catch_up_savings,
    complete_setup,
    persist_index,
    raise_on_embed_failure,
    register_clients,
    save_run_config,
    write_run_state,
)
from ._ingest import index_repository
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
    effective_run_mode_for_resume,
)
from .reporting import show_analysis_summary
from .workspace import _workspace_init


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


def _render_template_wiki(run: InitRun, *, embedder_was_requested: bool) -> None:
    """Render the whole wiki from structure and remember the embedder it used."""
    opts = run.opts
    run.index_only_embedder = _run_deterministic_generation_phase(
        repo_path=run.repo_path,
        result=run.result,
        total_phases=run.total_phases,
        concurrency=opts.concurrency,
        language=run.language,
        onboarding=opts.onboarding,
        wiki_style=opts.wiki_style,
        max_file_pages=run.max_file_pages,
        embedder_name_resolved=run.embedder_name_resolved,
        embedder_was_requested=embedder_was_requested,
        resume=opts.resume,
        timings=run.recorder.table,
        warnings=run.run_warnings,
    )


def _generate_with_model(run: InitRun) -> bool:
    """The priced branch. True when it was a dry run and init should stop."""
    opts = run.opts
    gen_stop, cost_declined = _run_generation_phase(
        repo_path=run.repo_path,
        result=run.result,
        provider=run.provider,
        total_phases=run.total_phases,
        concurrency=opts.concurrency,
        language=run.language,
        resolved_reasoning=run.resolved_reasoning,
        onboarding=opts.onboarding,
        wiki_style=opts.wiki_style,
        max_file_pages=run.max_file_pages,
        yes=opts.yes,
        dry_run=opts.dry_run,
        skip_tests=opts.skip_tests,
        skip_infra=opts.skip_infra,
        embedder_name_resolved=run.embedder_name_resolved,
        # A template wiki fills the store resume reads, so never resume over it.
        resume=opts.resume and run.prior_docs_mode != "deterministic",
        warnings=run.run_warnings,
        test_run=opts.test_run,
        timings=run.recorder.table,
    )
    if gen_stop:
        return True
    if cost_declined and run.prior_docs_mode == "llm":
        # Declining new pages is no request to replace the written ones.
        console.print(
            "  [dim]Keeping the wiki this repo already has. Nothing was "
            "generated and nothing was replaced.[/dim]"
        )
    elif cost_declined:
        run.cost_declined = True
        console.print(
            "  [dim]Building the wiki from structure instead. Run "
            "[bold]repowise update --full[/bold] to write it with a "
            "model later.[/dim]"
        )
        # Declining the bill keeps the configured embedder.
        _render_template_wiki(run, embedder_was_requested=True)
    return False


def _generate(run: InitRun) -> bool:
    """Phase 3. True when init should stop here (a dry run)."""
    opts = run.opts
    # The priced branch has its own dry-run exit.
    if opts.dry_run and (opts.index_only or run.no_provider):
        if opts.run_mode == "fast":
            console.print("\n  Dry run: fast mode indexes graph and git only. No wiki written.")
        else:
            _print_structural_plan(
                result=run.result,
                repo_path=run.repo_path,
                concurrency=opts.concurrency,
                language=run.language,
                onboarding=opts.onboarding,
                wiki_style=opts.wiki_style,
                max_file_pages=run.max_file_pages,
                skip_tests=opts.skip_tests,
                skip_infra=opts.skip_infra,
            )
        return True

    # Fast mode is a graph-and-git index only.
    if opts.index_only and opts.run_mode == "fast":
        console.print(
            "  [dim]Skipping wiki generation in fast mode. Run "
            "[bold]repowise init[/bold] without [bold]--mode fast[/bold] to "
            "render it from structure, or [bold]repowise update --full[/bold] "
            "to write it with a model.[/dim]"
        )
        return False
    if opts.index_only or run.no_provider:
        _render_template_wiki(run, embedder_was_requested=run.embedder_was_requested)
        return False
    return _generate_with_model(run)


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
    if run is None or not index_repository(run):
        return

    show_analysis_summary(run.result)
    if _generate(run):
        return

    # A declined cost gate or a missing provider persists like index-only.
    run.effective_index_only = run.opts.index_only or run.cost_declined or run.no_provider
    persist_index(run)
    catch_up_savings(run.repo_path)
    save_run_config(run)
    register_clients(run)
    write_run_state(run)
    complete_setup(run)
    raise_on_embed_failure(run)
