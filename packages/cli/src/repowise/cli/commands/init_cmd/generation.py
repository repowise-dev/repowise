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
from collections import Counter
from pathlib import Path
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
from repowise.cli.ui import (
    BRAND_STYLE,
    OK,
    OWL_SPINNER,
    WARN,
    MaybeCountColumn,
    RichProgressCallback,
)
from repowise.core.generation.models import (
    STUB_FALLBACK_ERROR,
    count_stub_fallbacks,
    is_stub_fallback,
)

__all__ = [
    "COST_GATE_USD",
    "CostGateDeclined",
    "announce_file_page_cap",
    "concept_page_count",
    "confirm_cost_gate",
    "cost_gate_blocks",
    "cost_gate_declined",
    "estimate_generation",
    "format_cost",
    "page_type_label",
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


# Every page type, in the order it reads best, with the two plain-English forms
# the init screen needs: a row label for the generation plan table, and a short
# noun for the one-line structural summary under it. One table so the plan, the
# summary and the cost question can never call the same page three things —
# ``module_page`` in the table, "concept pages" in the price, "subsystem pages"
# in the mode panel was the old state.
_PAGE_TYPE_LABELS: tuple[tuple[str, str, str], ...] = (
    ("module_page", "Subsystem pages", "subsystem"),
    ("repo_overview", "Repo overview", "overview"),
    ("architecture_diagram", "Architecture diagram", "diagram"),
    ("onboarding", "Onboarding pages", "onboarding"),
    ("file_page", "File pages", "file"),
    ("api_contract", "API contract pages", "API"),
    ("symbol_spotlight", "Symbol spotlights", "symbol"),
    ("scc_page", "Dependency cycle pages", "cycle"),
    ("layer_page", "Layer pages", "layer"),
    ("infra_page", "Infrastructure pages", "infra"),
)

_ROW_LABELS: dict[str, str] = {pt: row for pt, row, _ in _PAGE_TYPE_LABELS}


def page_type_label(page_type: str) -> str:
    """Plain-English row label for a page type id.

    Falls back to a readable form of the id itself, so a page type added to the
    generator but not to the table above degrades to ``"Scc page"`` rather than
    to a silent hole.
    """
    return _ROW_LABELS.get(page_type) or page_type.replace("_", " ").capitalize()


def structural_page_summary(plans: list[Any]) -> str:
    """Return e.g. ``"3622 pages rendered from structure, free: 2947 file, ..."``.

    The headline cost question names only the pages a model writes, which leaves
    the much larger total looking unexplained — and hides that repowise renders
    file, API and symbol pages from the code itself with no provider call. This
    is the one line that says so. Empty string when there are none.
    """
    from repowise.core.cost_estimator import STRUCTURAL_PAGE_TYPES

    counts = {p.page_type: p.count for p in plans if p.page_type in STRUCTURAL_PAGE_TYPES}
    total = sum(counts.values())
    if not total:
        return ""
    parts = [f"{counts[pt]} {short}" for pt, _, short in _PAGE_TYPE_LABELS if counts.get(pt)]
    return f"{total} more pages rendered from the code itself, no model, no cost: " + ", ".join(
        parts
    )


def announce_file_page_cap(parsed_files: list[Any], gen_config: Any) -> None:
    """Say so when the file bucket is being bounded, and how to undo it.

    A cap the user chose needs no explanation, but the one the size policy applies
    on its own would otherwise show up only as a page count nobody asked for. Both
    are printed, since a run that emits fewer pages than there are files should say
    which files it kept and how to ask for all of them.
    """
    from repowise.core.generation.selection import auto_file_page_cap, count_documentable_files

    requested = getattr(gen_config, "max_file_pages", None)
    if requested == 0:
        return  # explicitly uncapped: nothing is being held back
    documentable = count_documentable_files(parsed_files)
    cap = requested if requested else auto_file_page_cap(documentable)
    if not cap or cap >= documentable:
        return

    reason = "this repo's size" if requested is None else "your setting"
    console.print(
        f"  [dim]File pages: keeping the top [bold]{cap:,}[/bold] of about "
        f"{documentable:,} by importance ({reason}). File pages cost no model "
        "tokens, so this bounds wiki size and search noise, not spend. Pass "
        "[bold]--max-file-pages 0[/bold] for one page per file.[/dim]"
    )


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
                f"  [{OK}]✓[/] KG enriched: "
                f"{len(enriched.layers)} layers, {len(enriched.tour)} tour steps"
            )
        except Exception as exc:
            console.print(f"  [{WARN}]KG enrichment skipped: {exc}[/]")


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
    test_run: bool = False,
    timings: Any | None = None,
) -> list[Any]:
    """Generate wiki pages for one repo and enrich its knowledge graph.

    Builds the embedder + vector store + cost tracker, runs the resume-friendly
    generation wrapper, enriches the KG, and flushes buffered cost rows in one
    transaction (kept out of the contended generation window, issue #326).

    Mutates ``result`` in place with ``generated_pages``, ``preserved_page_ids``
    (the pages a resumed run skipped, which persistence must not sweep) and
    ``vector_store``
    (the latter is shared so the Phase-2C decision dedup matches + embeds
    decisions into the same store the pages land in). Returns the pages.

    ``verbose`` controls only console output: the single-repo flow prints the
    page count + KG status; the workspace flow stays quiet and prints its own
    per-repo summary.

    ``timings`` is the run's :class:`PhaseTimings` table. Generation owns its
    own progress bar, so without it the phases here land nowhere.
    """
    from repowise.core.pipeline import PhaseTimingRecorder, timed

    from ._generation_persist import run_generation_with_persistence

    if verbose:
        announce_file_page_cap(result.parsed_files, gen_config)

    embedder_impl: Any = build_embedder(embedder_name_resolved, repo_path)
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
        columns.append(TextColumn("[" + OK + "]${task.fields[cost]:.3f}[/]"))

    # Filled by a resumed run with the pages it skipped because they already
    # exist. Persistence needs them: they are absent from ``generated_pages``,
    # and the stale sweep reads absence as "delete me" (issue #1089).
    #
    # Attached to the result BEFORE generation runs, not after. The workspace
    # flow catches a generation failure and persists anyway, so an assignment
    # that trails the run would hand the sweep an empty set on exactly the
    # broken runs this exists to protect. Same object, so what the run fills in
    # is what persistence reads however the block exits.
    preserved_page_ids: set[str] = set()
    result.preserved_page_ids = preserved_page_ids

    with Progress(*columns, console=console) as gen_progress:
        gen_callback: Any = RichProgressCallback(gen_progress, console)
        if timings is not None:
            gen_callback = PhaseTimingRecorder(gen_callback, timings)
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
                preserved_page_ids=preserved_page_ids,
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
                test_run=test_run,
            )
        )

    jobs_dir = Path(repo_path) / ".repowise" / "jobs"
    failed_page_ids: list[str] = []
    if jobs_dir.exists():
        with contextlib.suppress(Exception):
            from repowise.core.generation import JobSystem

            js = JobSystem(jobs_dir)
            job_id = getattr(result, "job_id", None)
            if job_id:
                failed_page_ids = js.get_checkpoint(job_id).failed_page_ids
            else:
                jobs = js.list_jobs()
                if jobs:
                    failed_page_ids = jobs[0].failed_page_ids

    result.generated_pages = generated_pages
    result.failed_page_ids = failed_page_ids

    # A page whose provider call failed is still handed back, as a stub rendered
    # from structure alone, so that the row exists and a later run can find it
    # and refill it. That makes it a member of ``generated_pages`` like any
    # other page, and counting the list is what let the summary report the same
    # page as generated and as failed at once.
    stub_count = count_stub_fallbacks(generated_pages)
    written_count = len(generated_pages) - stub_count

    if failed_page_ids:
        type_counts = Counter(pid.split(":")[0] for pid in failed_page_ids)
        console.print(
            f"  [{WARN}]⚠[/] Generated [bold]{written_count}[/bold] pages "
            f"([bold yellow]{len(failed_page_ids)} failed[/bold yellow])\n"
        )
        console.print("  [bold yellow]Failed pages by type:[/bold yellow]")
        for page_type, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            console.print(f"    • {page_type}: {count}")
        # Saying only "missing" sends the user looking for absent pages and
        # finding present ones, which reads as the report being wrong. Most
        # failures leave a placeholder behind; --resume replaces those too,
        # because a placeholder is deliberately kept out of the record of what
        # is already done.
        placeholder_note = (
            f"  [dim]{stub_count} of them left a placeholder page rendered from "
            "structure alone, so the wiki has a page there but not a written "
            "one. The rest produced no page at all.[/dim]\n"
            if stub_count
            else ""
        )
        # Not necessarily the provider's fault: the commonest cause is this
        # run's own artifact check rejecting the text the model returned. The
        # old wording named the provider unconditionally, which sent people to
        # check their key and their status page over a quality gate firing.
        # The per-page reason is on the stub, so quote it instead of guessing.
        reasons = sorted(
            {
                str(p.metadata.get(STUB_FALLBACK_ERROR, "")).strip()
                for p in generated_pages
                if is_stub_fallback(p)
            }
            - {""}
        )
        reason_note = "".join(f"  [dim]· {r[:160]}[/dim]\n" for r in reasons[:3])
        console.print(
            f"\n  [{WARN}]The wiki is incomplete: some pages were not written.[/]\n"
            f"{placeholder_note}"
            f"{reason_note}"
            "  [dim]Run [bold]repowise init --resume[/bold] to generate the pages that "
            "failed, without re-spending on the ones that succeeded.[/dim]\n"
        )
    elif verbose:
        console.print(f"  [{OK}]✓[/] Generated [bold]{written_count}[/bold] pages")

    # KG enrichment is layer naming and the guided tour, both pure prompting.
    # A deterministic run has no model to ask, and the skeleton's structural
    # layers stand on their own.
    if not deterministic:
        with timed(timings, "generation.kg_enrich"):
            _enrich_knowledge_graph(
                result=result,
                provider=provider,
                gen_config=gen_config,
                generated_pages=generated_pages,
                verbose=verbose,
            )
        flush_cost_tracker(cost_tracker)

    # What the run actually spent, for the completion panel. The user was shown
    # an estimate before the run and a live cost column during it; reporting
    # only tokens at the end left the one number they were promised unanswered.
    #
    # Read AFTER enrichment, not before: layer naming and the guided tour are
    # real model calls billed through this same tracker, so capturing it any
    # earlier would print a figure the llm_costs table disagrees with.
    result.llm_cost_usd = cost_tracker.session_cost if cost_tracker is not None else 0.0
    return generated_pages
