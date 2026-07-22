"""Interactive scope chooser for a bare ``repowise generate``.

When a user runs ``generate`` with no selection flag on a terminal, we do not
default to writing every unwritten page — on a 3,000-file index-only repo that
is one Enter from a very large bill. Instead we show the wiki's current state,
offer the same coverage menu ``repowise init`` uses (page counts + cost per
tier, recommended default 20%), then ask about cascade only when it would change
which pages get written, and finally the normal cost gate.

The coverage table and its prompt are the exact ones ``init`` renders
(:mod:`repowise.cli.coverage_select`); the per-tier counts and costs come from
the shared :func:`compute_coverage_options`. So init and generate present one
chooser, not two look-alikes.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.prompt import Prompt

from repowise.cli.coverage_select import interactive_coverage_select
from repowise.core.cost_estimator import compute_coverage_options
from repowise.core.generation.cascade import CascadeMode, PageDependencies, expand_cascade
from repowise.core.generation.page_selection import PageRecord

from .scope import build_ranked_seed

# Coverage tiers offered by the generate chooser. Wider at the top than init's
# ladder because a generate run is an explicit upgrade step, and it ends in an
# ``All`` (100%) row so "write everything unwritten" is a deliberate pick, never
# the Enter-through default. 20% stays the recommended default.
GENERATE_COVERAGE_PCTS: tuple[float, ...] = (0.10, 0.20, 0.30, 0.50, 1.00)
RECOMMENDED_PCT: float = 0.20

_CASCADE_MODES: tuple[CascadeMode, ...] = ("none", "dependents", "full")


@dataclass(frozen=True)
class InteractiveChoice:
    """The scope a bare ``generate`` resolved to through the chooser."""

    ranked_seed: set[str]
    cascade_mode: CascadeMode


def print_wiki_state(console: Console, records: list[PageRecord]) -> None:
    """Print the written / unwritten / stale breakdown of the current wiki.

    ``written`` + ``unwritten`` partition the wiki and sum to the total; ``stale``
    is a cross-cutting count (a page of either kind whose code moved on), shown
    after a separator so it does not read as a third slice of the total.
    """
    total = len(records)
    template = sum(1 for r in records if r.is_template)
    written = total - template
    stale = sum(1 for r in records if r.is_stale)
    line = (
        f"[bold]Wiki:[/bold] {total} pages — "
        f"[cyan]{written}[/cyan] written, [yellow]{template}[/yellow] unwritten"
    )
    if stale:
        line += f"  ·  [red]{stale}[/red] stale (code changed since written)"
    console.print(line + ".")


def choose_cascade(
    console: Console,
    seed_ids: set[str],
    deps: PageDependencies,
    *,
    default: CascadeMode = "none",
) -> CascadeMode:
    """Ask how to treat the pages that summarize the chosen set.

    Returns *default* without prompting when every cascade mode writes the same
    pages (the "only when it changes the outcome" rule) — which is the common
    case for a coverage pick, whose set already includes the module and
    repo-wide pages. When the modes diverge, the extra page counts are shown so
    the choice is concrete.
    """
    generated = {m: expand_cascade(seed_ids, m, deps).generate_ids for m in _CASCADE_MODES}
    if len({frozenset(ids) for ids in generated.values()}) == 1:
        return default

    base = len(generated["none"])
    console.print("\n[bold]Cascade[/bold] — the pages that summarize the ones you are writing:")
    labels = {
        "none": "write exactly these pages; mark the rest stale",
        "dependents": "also rewrite the module / cycle / layer pages that contain them",
        "full": "also rewrite the repo-wide overview, architecture and onboarding",
    }
    choices: list[str] = []
    default_choice = "1"
    for idx, mode in enumerate(_CASCADE_MODES, start=1):
        extra = len(generated[mode]) - base
        suffix = f" [dim](+{extra} pages)[/dim]" if extra > 0 else ""
        rec = " [dim](default)[/dim]" if mode == default else ""
        console.print(f"  [{idx}] [bold]{mode}[/bold] — {labels[mode]}{suffix}{rec}")
        choices.append(str(idx))
        if mode == default:
            default_choice = str(idx)

    picked = Prompt.ask("  Cascade", choices=choices, default=default_choice, console=console)
    return _CASCADE_MODES[int(picked) - 1]


def run_interactive_chooser(
    console: Console,
    *,
    records: list[PageRecord],
    parsed_files: list,
    graph_builder: object,
    config: object,
    kg_ctx: object,
    provider: object,
    repo_path: object,
    repo_name: str,
    deps: PageDependencies,
) -> InteractiveChoice | None:
    """Drive the full bare-``generate`` chooser and return the resolved scope.

    Returns ``None`` when there is nothing to write (no unwritten pages, or the
    user's coverage pick lands entirely on already-written pages).
    """
    print_wiki_state(console, records)
    if not any(r.is_template for r in records):
        console.print("[green]Every page is already written.[/green] Nothing to upgrade.")
        return None

    options = compute_coverage_options(
        parsed_files=parsed_files,
        graph_builder=graph_builder,
        base_config=config,
        provider_name=provider.provider_name,  # type: ignore[attr-defined]
        model_name=provider.model_name,  # type: ignore[attr-defined]
        repo_path=repo_path,
        percentages=GENERATE_COVERAGE_PCTS,
        recommended=RECOMMENDED_PCT,
    )
    # The menu sizes each tier for a full wiki (it is blind to what is already
    # written). When some pages are written, the run skips those, so the real
    # count and cost (shown in the plan + at the confirm) are lower. Say so, so
    # the table's numbers do not read as a contradiction of the final estimate.
    if any(not r.is_template for r in records):
        console.print(
            "  [dim]Counts below size each tier for a full wiki; already-written "
            "pages are skipped, so the final estimate (shown before you confirm) "
            "is lower.[/dim]"
        )
    chosen = interactive_coverage_select(console, options, deterministic_tail=True)

    ranked_seed = build_ranked_seed(
        parsed_files=parsed_files,
        graph_builder=graph_builder,
        config=config,
        kg_ctx=kg_ctx,
        records=records,
        repo_name=repo_name,
        coverage_pct=chosen.pct,
    )
    if not ranked_seed:
        console.print(
            "[yellow]Everything in that coverage is already written.[/yellow] Nothing to generate."
        )
        return None

    cascade_mode = choose_cascade(console, ranked_seed, deps)
    return InteractiveChoice(ranked_seed=ranked_seed, cascade_mode=cascade_mode)
