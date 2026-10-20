"""Phase 3 of a single-repo init: the wiki from structure, or with a model."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from rich.table import Table

from repowise.cli.cost_gate import spend_phrase
from repowise.cli.helpers import console, load_config, warn
from repowise.cli.ui import BRAND, VALUE, print_stage
from repowise.cli.ui.mode_names import INDEX_ONLY, provider_model
from repowise.core.generation.styles import DEFAULT_STYLE

from ._exit import COST_UNCONFIRMED_BUILT, COST_UNCONFIRMED_KEPT
from ._options import InitRun
from .generation import (
    concept_page_count,
    cost_gate_declined,
    embedding_note,
    estimate_generation,
    page_type_label,
    run_repo_generation,
    structural_page_summary,
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
    table.add_column("Pages", style=VALUE)
    table.add_column("Count", justify="right")
    total = 0
    for plan in plans:
        total += plan.count
        table.add_row(page_type_label(plan.page_type), f"{plan.count:,}")
    table.add_section()
    table.add_row("[bold]Total[/bold]", f"[bold]{total:,}[/bold]")
    console.print(table)
    console.print("  Dry run: every page above is built from code. No documentation written.")


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

    Every page type has a deterministic renderer, so an index-only run is a
    complete wiki derived from structure. No provider, no cost, nothing to gate.

    Returns the embedder actually used, which the caller persists so a later
    ``repowise update`` embeds the same way rather than re-deciding.
    """
    from repowise.cli.providers import template_run_embedder
    from repowise.core.generation import GenerationConfig
    from repowise.core.providers.llm.template import TemplateProvider

    embedder = template_run_embedder(embedder_name_resolved, embedder_was_requested)

    print_stage(
        console,
        "generation",
        total_phases,
        "Building documentation from code (no model, no cost)",
    )
    # Both knobs only reach a model: templates are English and unstyled, so say
    # so rather than record a style the pages do not have.
    if language != "en":
        console.print(
            f"  [dim]Documentation built from code is in English, so [bold]--language "
            f"{language}[/bold] has no effect here. Run [bold]repowise update "
            "--full[/bold] to have a model write the documentation in that language.[/dim]"
        )
    if wiki_style != DEFAULT_STYLE:
        console.print(
            f"  [dim]Documentation styles shape how a model writes, so [bold]--wiki-style "
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
        total_phases=total_phases,
    )
    return embedder


def _local_server_overloaded(provider_name: str, concurrency: int) -> bool:
    """A local model server (Ollama) at high concurrency, which can time out.

    An agent CLI bounds its own process count (its estimate says so), so it
    never needs the advice to lower --concurrency.
    """
    from repowise.core.providers.llm.specs import PROVIDER_SPECS

    spec = PROVIDER_SPECS.get(provider_name)
    is_local_server = spec is not None and spec.local and not spec.agent
    return is_local_server and concurrency > 4


def _print_generation_plan(
    est: Any, result: Any, provider: Any, *, concurrency: int, onboarding: bool
) -> None:
    """The priced plan: pages by type and author, languages, and onboarding."""
    from repowise.core.cost_estimator import STRUCTURAL_PAGE_TYPES

    # "Written by" answers the question the price below raises: most of this
    # total is free.
    table = Table(title="Generation Plan", border_style=BRAND)
    table.add_column("Pages", style=VALUE)
    table.add_column("Count", justify="right")
    table.add_column("Written by")
    model_pages = 0
    for plan in est.plans:
        from_model = plan.page_type not in STRUCTURAL_PAGE_TYPES
        model_pages += plan.count if from_model else 0
        table.add_row(
            page_type_label(plan.page_type),
            f"{plan.count:,}",
            "model" if from_model else "[dim]code[/dim]",
        )
    table.add_section()
    table.add_row(
        "[bold]Total[/bold]",
        f"[bold]{est.total_pages:,}[/bold]",
        f"[bold]{model_pages:,} by model, {est.total_pages - model_pages:,} from code[/bold]",
    )
    console.print(table)

    # Language breakdown
    lang_dist = result.repo_structure.root_language_distribution
    if lang_dist:
        lang_items = sorted(lang_dist.items(), key=lambda x: -x[1])[:6]
        lang_parts = [f"{lang} {pct:.0%}" for lang, pct in lang_items]
        console.print(f"  Languages: {', '.join(lang_parts)}")

    if _local_server_overloaded(provider.provider_name, concurrency):
        warn(
            f"  {provider.provider_name} is a local provider "
            f"running with concurrency={concurrency}. "
            f"If you see timeout errors, try [bold]--concurrency 1[/bold]."
        )

    if onboarding:
        console.print(
            "  [dim]Plus up to 8 curated onboarding pages (overview, architecture, "
            "getting started, and so on). Slots without enough signal are skipped.[/dim]"
        )
    else:
        console.print("  [dim]Onboarding collection: disabled (--no-onboarding).[/dim]")
    console.print()


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
    from repowise.core.generation import GenerationConfig

    print_stage(
        console,
        "generation",
        total_phases,
        f"Writing documentation with {provider_model(provider.provider_name, provider.model_name)}",
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
        embedder=embedder_name_resolved,
    )

    _print_generation_plan(est, result, provider, concurrency=concurrency, onboarding=onboarding)

    if dry_run:
        console.print("  Dry run: no pages generated.")
        return True, False

    # The single cost question. It declines rather than raises when it cannot
    # ask: init's index is not yet persisted, so raising would discard the run;
    # the caller's fallback renders the structural wiki and saves the index.
    # `generate` and `update --full` raise instead: their index already exists.
    concept_n = concept_page_count(plans)
    console.print(
        f"  Writing [bold]{concept_n:,}[/bold] subsystem pages with "
        f"[{VALUE}]{provider.model_name}[/]. {spend_phrase(est, concurrency)}."
    )
    structural = structural_page_summary(plans)
    if structural:
        console.print(f"  [dim]{structural}[/dim]")
    embedding = embedding_note(est)
    if embedding:
        console.print(f"  [dim]{embedding}[/dim]")
    if cost_gate_declined(est, yes=yes, message="  Continue?"):
        console.print("  Skipping the model. [dim]Building documentation from code instead.[/dim]")
        console.print(
            "  [dim]Run [bold]repowise generate[/bold] any time to write the subsystem "
            "pages, or re-run init with [bold]--yes[/bold] to accept the cost now.\n"
            "  Post-commit updates stay built from code, so no hook will start a model run "
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
        total_phases=total_phases,
    )
    return False, False


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
    if cost_declined:
        _after_cost_declined(run)
    return False


def _after_cost_declined(run: InitRun) -> None:
    """Keep model-written pages, or build from code; a gap when nobody could say no."""
    kept = run.prior_docs_mode == "llm"
    if not sys.stdin.isatty():
        run.gaps.append(COST_UNCONFIRMED_KEPT if kept else COST_UNCONFIRMED_BUILT)
    if kept:
        # Declining new pages is no request to replace the written ones.
        console.print(
            "  [dim]Keeping the documentation this repo already has. Nothing was "
            "generated and nothing was replaced.[/dim]"
        )
        return
    run.cost_declined = True
    console.print(
        "  [dim]Building documentation from code instead. Run "
        "[bold]repowise update --full[/bold] to write it with a "
        "model later.[/dim]"
    )
    # Declining the bill keeps the configured embedder.
    _render_template_wiki(run, embedder_was_requested=True)


def _generate(run: InitRun) -> bool:
    """Phase 3. True when init should stop here (a dry run)."""
    opts = run.opts
    # The priced branch has its own dry-run exit.
    if opts.dry_run and (opts.index_only or run.no_provider):
        if opts.run_mode == "fast":
            console.print(
                f"\n  Dry run: {INDEX_ONLY.lower()}, fast mode indexes graph and git. "
                "No documentation written."
            )
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
        from repowise.core.index_scope import FAST_TO_FULL_COMMAND

        console.print(
            f"  [dim]{INDEX_ONLY}: fast mode skips documentation. Run "
            f"[bold]{FAST_TO_FULL_COMMAND}[/bold] to build it from code (no key "
            "needed), or have a model write it when one is configured.[/dim]"
        )
        return False
    if opts.index_only or run.no_provider:
        _render_template_wiki(run, embedder_was_requested=run.embedder_was_requested)
        return False
    return _generate_with_model(run)
