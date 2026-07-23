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
from typing import Any

from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

# The cost-gate helpers moved to ``repowise.cli.cost_gate`` so ``init`` and
# ``generate`` share one gate. Re-exported here for the callers that still
# import them from this module (init's command + workspace flows).
from repowise.cli.cost_gate import (
    COST_GATE_USD,
    CostGateDeclined,
    confirm_cost_gate,
    cost_gate_blocks,
    cost_gate_declined,
    format_cost,
)
from repowise.cli.helpers import console, run_async
from repowise.cli.providers import (
    build_cost_tracker,
    build_embedder,
    build_vector_store,
    flush_cost_tracker,
)
from repowise.cli.ui import BRAND_STYLE, OWL_SPINNER, MaybeCountColumn, RichProgressCallback

__all__ = [
    "COST_GATE_USD",
    "CostGateDeclined",
    "concept_page_count",
    "confirm_cost_gate",
    "cost_gate_blocks",
    "cost_gate_declined",
    "estimate_generation",
    "format_cost",
    "run_repo_generation",
]


def estimate_generation(
    *,
    result: Any,
    gen_config: Any,
    provider: Any,
    repo_path: Any,
    skip_tests: bool,
    skip_infra: bool,
) -> tuple[list[Any], Any]:
    """Build the generation plan and price it. One plan, one estimate.

    Every structural page type is free, and the concept tree is a total cover of
    the production files, so there is nothing to ration and no coverage level to
    pick. The spend is the concept tree plus the repo-wide synthesis pages, and
    it is a single number.

    Returns ``(plans, estimate)``.
    """
    from repowise.cli.cost_estimator import build_generation_plan, estimate_cost

    # Curated modules from the in-memory index result, so the plan/cost
    # estimate selects the same module set generation will (the artifact
    # file is not on disk yet during a fresh init).
    kg_modules = getattr(getattr(result, "knowledge_graph_result", None), "modules", None) or None

    plans = build_generation_plan(
        result.parsed_files,
        result.graph_builder,
        gen_config,
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
    return plans, est


def concept_page_count(plans: list[Any]) -> int:
    """The number of concept pages a model writes, for the cost question.

    ``module_page`` is the concept tree; it dominates the bill and is the count
    the question names. ``repo_overview``, ``architecture_diagram`` and
    ``onboarding`` also cost tokens but are few and fixed, so they ride inside
    the dollar estimate rather than the headline count.
    """
    return next((p.count for p in plans if p.page_type == "module_page"), 0)


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

    deterministic = bool(getattr(gen_config, "deterministic", False))

    # Cost tracker backed by the real DB so every LLM call is persisted to the
    # llm_costs table. Attached to the provider unconditionally (all providers
    # accept ``_cost_tracker`` as an attribute). A deterministic run makes no
    # calls, so it gets none: an empty ledger, not a ledger of zeroes.
    cost_tracker = None if deterministic else build_cost_tracker(repo_path, result.repo_name)
    provider._cost_tracker = cost_tracker

    if verbose and not deterministic:
        console.print(
            "  [dim](each generated page is saved as it completes — safe to Ctrl-C, "
            "then run 'repowise init --resume' to pick up where it stopped)[/dim]"
        )

    # No cost column on a deterministic run: it would sit at $0.000 for the
    # whole run, which reads as an unanswered question rather than an answer.
    columns: list[Any] = [
        SpinnerColumn(spinner_name=OWL_SPINNER, style=BRAND_STYLE),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MaybeCountColumn(),
        TimeElapsedColumn(),
    ]
    if not deterministic:
        columns.append(TextColumn("[green]${task.fields[cost]:.3f}[/green]"))

    with Progress(*columns, console=console) as gen_progress:
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
                    getattr(getattr(result, "knowledge_graph_result", None), "modules", None)
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

    # KG enrichment is layer naming and the guided tour, both pure prompting.
    # A deterministic run has no model to ask, and the skeleton's structural
    # layers stand on their own.
    if not deterministic:
        _enrich_knowledge_graph(
            result=result,
            provider=provider,
            gen_config=gen_config,
            generated_pages=generated_pages,
            verbose=verbose,
        )
        flush_cost_tracker(cost_tracker)
    return generated_pages
