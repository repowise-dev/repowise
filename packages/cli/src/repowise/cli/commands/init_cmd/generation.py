"""Shared LLM page-generation core for ``repowise init``.

Both the single-repo flow (:mod:`.command`) and the per-repo workspace flow
(:mod:`.workspace`) need the same four steps — pick a coverage level, estimate
cost, gate on the estimate, then run generation + knowledge-graph enrichment and
flush the cost ledger. Those steps used to be copy-pasted across the two flows;
they now live here once, with the callers supplying only their distinct
rendering and control-flow (the single-repo flow prints a full plan table and
returns a "declined" flag; the workspace flow prints a compact line and raises
:class:`CostGateDeclined`).
"""

from __future__ import annotations

import contextlib
import sys
from dataclasses import replace as _replace
from typing import Any

import click
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from repowise.cli.helpers import console, run_async
from repowise.cli.providers import (
    build_cost_tracker,
    build_embedder,
    build_vector_store,
    flush_cost_tracker,
)
from repowise.cli.ui import BRAND_STYLE, OWL_SPINNER, MaybeCountColumn, RichProgressCallback

# The LLM-cost confirmation threshold. A run whose estimate exceeds this asks
# for confirmation (unless ``--yes``); below it generation proceeds silently.
COST_GATE_USD = 2.00


class CostGateDeclined(Exception):  # noqa: N818 — a control-flow signal, not an error
    """Raised when the user answers No at the LLM-cost confirmation prompt.

    Carries no payload — the caller just needs to know that generation was
    declined so it can persist state in index-only shape (no docs) and
    return cleanly. Using an exception (vs. a sentinel return value) lets
    us bail out of nested generation flows without rethreading return
    types through every helper.
    """


def confirm_cost_gate(message: str) -> bool:
    """Render the cost-gate ``[Y/n]`` prompt with visual padding.

    Click's plain ``confirm`` interleaves with the trailing line of any
    prior Rich output (progress-bar frames, status spinners), making the
    confirm glyphs hard to spot — users have walked past it and approved
    a $14 bill thinking they were still in cost-estimate territory. A
    blank line + horizontal rule cleanly separates the prompt from
    whatever was printed above it.

    Default is Yes: the user just picked a coverage tier with the cost
    range printed next to it, so Enter-through should continue the run
    they configured — the gate exists to make the spend visible, not to
    interrupt it.
    """
    console.line()
    console.rule(style="yellow")
    return click.confirm(message, default=True)


def cost_gate_declined(est: Any, *, yes: bool, message: str) -> bool:
    """Return ``True`` when the run should skip generation on cost grounds.

    Only prompts when the estimate clears :data:`COST_GATE_USD` and ``--yes``
    was not passed; a declined prompt yields ``True``.
    """
    return est.estimated_cost_usd > COST_GATE_USD and not yes and not confirm_cost_gate(message)


def format_cost(est: Any) -> str:
    """Render an estimate as a human-readable USD string (range when known)."""
    if est.cost_range is not None:
        cost_str = (
            f"${est.cost_range.low:.2f} - ${est.cost_range.high:.2f} USD "
            f"(median ${est.estimated_cost_usd:.2f})"
        )
        if est.is_calibrated:
            cost_str += " [calibrated]"
        return cost_str
    return f"${est.estimated_cost_usd:.2f} USD"


def select_coverage(
    *,
    result: Any,
    gen_config: Any,
    provider: Any,
    repo_path: Any,
    skip_tests: bool,
    skip_infra: bool,
    coverage_pct: float | None,
    yes: bool,
) -> tuple[float, list[Any], Any, Any]:
    """Pick a coverage level and estimate its cost.

    Renders the interactive coverage chooser when stdin is a TTY and no
    explicit ``--coverage`` was passed (and not ``--yes``); otherwise the
    configured / default percentage drives a single non-interactive estimate.

    Returns ``(chosen_pct, plans, estimate, gen_config)`` where ``gen_config``
    has the chosen coverage baked in so the page generator honours the pick.
    """
    from repowise.cli.cost_estimator import (
        build_generation_plan,
        compute_coverage_options,
        estimate_cost,
    )
    from repowise.cli.coverage_select import interactive_coverage_select

    # Curated modules from the in-memory index result, so the plan/cost
    # estimate selects the same module set generation will (the artifact
    # file is not on disk yet during a fresh init).
    kg_modules = (
        getattr(getattr(result, "knowledge_graph_result", None), "modules", None) or None
    )

    if sys.stdin.isatty() and coverage_pct is None and not yes:
        options = compute_coverage_options(
            parsed_files=result.parsed_files,
            graph_builder=result.graph_builder,
            base_config=gen_config,
            provider_name=provider.provider_name,
            model_name=provider.model_name,
            repo_path=repo_path,
            skip_tests=skip_tests,
            skip_infra=skip_infra,
            kg_modules=kg_modules,
        )
        chosen = interactive_coverage_select(console, options)
        chosen_pct = chosen.pct
        plans = chosen.plans
        est = chosen.estimate
    else:
        chosen_pct = coverage_pct if coverage_pct is not None else gen_config.coverage_pct
        gen_config_for_plan = _replace(
            gen_config, coverage_pct=chosen_pct, max_pages_pct=chosen_pct
        )
        plans = build_generation_plan(
            result.parsed_files,
            result.graph_builder,
            gen_config_for_plan,
            skip_tests,
            skip_infra,
            kg_modules=kg_modules,
        )
        est = estimate_cost(
            plans,
            provider.provider_name,
            provider.model_name,
            repo_path=repo_path,
        )

    # Bake the chosen coverage into the gen_config that runs generation, so the
    # page generator's selection layer honours the user's pick.
    gen_config = _replace(gen_config, coverage_pct=chosen_pct, max_pages_pct=chosen_pct)
    return chosen_pct, plans, est, gen_config


def _enrich_knowledge_graph(
    *,
    result: Any,
    provider: Any,
    gen_config: Any,
    generated_pages: list[Any],
    verbose: bool,
) -> None:
    """Best-effort KG enrichment (layers + tour) in place on ``result``.

    ``verbose`` renders a status spinner + outcome line (single-repo flow); the
    quiet path (workspace flow) swallows failures silently so one repo's KG
    error never aborts the workspace loop.
    """
    kg = getattr(result, "knowledge_graph_result", None)
    if kg is None or provider is None:
        return

    from repowise.core.generation.knowledge_graph import enrich_knowledge_graph

    def _run() -> Any:
        return run_async(
            enrich_knowledge_graph(
                kg_skeleton=kg,
                llm_client=provider,
                graph_builder=result.graph_builder,
                repo_structure=result.repo_structure,
                tech_stack=result.tech_stack,
                generated_pages=generated_pages,
                reasoning=gen_config.reasoning,
            )
        )

    if not verbose:
        with contextlib.suppress(Exception):
            result.knowledge_graph_result = _run()
        return

    with console.status("  Enriching knowledge graph (layers + tour)…", spinner=OWL_SPINNER):
        try:
            result.knowledge_graph_result = _run()
            enriched = result.knowledge_graph_result
            console.print(
                f"  [green]✓[/green] KG enriched: "
                f"{len(enriched.layers)} layers, {len(enriched.tour)} tour steps"
            )
        except Exception as exc:
            console.print(f"  [yellow]KG enrichment skipped: {exc}[/yellow]")


def run_repo_generation(
    *,
    repo_path: Any,
    result: Any,
    provider: Any,
    gen_config: Any,
    concurrency: int,
    embedder_name_resolved: str,
    resume: bool,
    verbose: bool,
) -> list[Any]:
    """Generate wiki pages for one repo and enrich its knowledge graph.

    Builds the embedder + vector store + cost tracker, runs the resume-friendly
    generation wrapper, enriches the KG, and flushes buffered cost rows in one
    transaction (kept out of the contended generation window, issue #326).

    Mutates ``result`` in place with ``generated_pages`` and ``vector_store``
    (the latter is shared so the Phase-2C decision dedup matches + embeds
    decisions into the same store the pages land in). Returns the pages.

    ``verbose`` controls only console output: the single-repo flow prints the
    page count + KG status; the workspace flow stays quiet and prints its own
    per-repo summary.
    """
    from ._generation_persist import run_generation_with_persistence

    embedder_impl: Any = build_embedder(embedder_name_resolved)
    vector_store: Any = build_vector_store(repo_path, embedder_impl)
    result.vector_store = vector_store

    # Cost tracker backed by the real DB so every LLM call is persisted to the
    # llm_costs table. Attached to the provider unconditionally (all providers
    # accept ``_cost_tracker`` as an attribute).
    cost_tracker = build_cost_tracker(repo_path, result.repo_name)
    provider._cost_tracker = cost_tracker

    if verbose:
        console.print(
            "  [dim](each generated page is saved as it completes — safe to Ctrl-C, "
            "then run 'repowise init --resume' to pick up where it stopped)[/dim]"
        )

    with Progress(
        SpinnerColumn(spinner_name=OWL_SPINNER, style=BRAND_STYLE),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MaybeCountColumn(),
        TimeElapsedColumn(),
        TextColumn("[green]${task.fields[cost]:.3f}[/green]"),
        console=console,
    ) as gen_progress:
        gen_callback = RichProgressCallback(gen_progress, console)
        generated_pages = run_async(
            run_generation_with_persistence(
                repo_path=repo_path,
                repo_name=result.repo_name,
                parsed_files=result.parsed_files,
                source_map=result.source_map,
                graph_builder=result.graph_builder,
                repo_structure=result.repo_structure,
                git_meta_map=result.git_meta_map,
                llm_client=provider,
                embedder=embedder_impl,
                vector_store=vector_store,
                concurrency=concurrency,
                progress=gen_callback,
                resume=resume,
                cost_tracker=cost_tracker,
                generation_config=gen_config,
                # In-memory curated modules: on a fresh init the
                # knowledge-graph.json artifact is only written during
                # persistence, AFTER this generation pass — without this the
                # kg_ctx file fallback is empty and module selection silently
                # degrades to community grouping.
                kg_modules=(
                    getattr(
                        getattr(result, "knowledge_graph_result", None), "modules", None
                    )
                    or None
                ),
                kg_data=(
                    result.knowledge_graph_result.to_dict()
                    if getattr(result, "knowledge_graph_result", None) is not None
                    else None
                ),
            )
        )

    result.generated_pages = generated_pages
    if verbose:
        console.print(f"  [green]✓[/green] Generated [bold]{len(generated_pages)}[/bold] pages")

    _enrich_knowledge_graph(
        result=result,
        provider=provider,
        gen_config=gen_config,
        generated_pages=generated_pages,
        verbose=verbose,
    )

    flush_cost_tracker(cost_tracker)
    return generated_pages
