"""Interactive helpers for a bare ``repowise generate``.

A bare ``generate`` on a terminal does not silently rewrite the whole wiki. It
shows the wiki's written / unwritten state, defaults to writing the concept
pages that are still stubs, asks about cascade only when it would change which
pages get written, and then puts the single cost question in front of the user.
Piped / ``--yes`` / flagged runs skip straight to that non-interactive default
(``--unwritten``).
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.prompt import Prompt

from repowise.core.generation.cascade import CascadeMode, PageDependencies, expand_cascade
from repowise.core.generation.page_selection import PageRecord

_CASCADE_MODES: tuple[CascadeMode, ...] = ("none", "dependents", "full")


@dataclass(frozen=True)
class InteractiveChoice:
    """The cascade a bare ``generate`` resolved to through the chooser."""

    cascade_mode: CascadeMode


def _concept_records(records: list[PageRecord]) -> list[PageRecord]:
    """The pages a model writes. Everything else is structural, and generate
    never touches it, so the wiki-state count and the "nothing unwritten" guard
    both read the concept layer only (a structural page is permanently a
    template, so counting it would always report the wiki as unwritten)."""
    from .engine import _MODEL_WRITTEN_PAGE_TYPES

    return [r for r in records if r.page_type in _MODEL_WRITTEN_PAGE_TYPES]


def print_wiki_state(console: Console, records: list[PageRecord]) -> None:
    """Print the written / unwritten / stale breakdown of the concept layer.

    Scoped to the pages a model writes: ``written`` + ``unwritten`` partition
    those and ``stale`` is a cross-cutting count of ones whose code moved on. The
    total wiki size (structural pages included) is shown for context.
    """
    concept = _concept_records(records)
    total = len(concept)
    template = sum(1 for r in concept if r.is_template)
    written = total - template
    stale = sum(1 for r in concept if r.is_stale)
    line = (
        f"[bold]Subsystem pages:[/bold] {total} of {len(records)} wiki pages. "
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
    default: CascadeMode = "dependents",
) -> CascadeMode:
    """Ask how to treat the pages that summarize the chosen set.

    Returns *default* without prompting when every cascade mode writes the same
    pages (the "only when it changes the outcome" rule). When the modes diverge,
    the extra page counts are shown so the choice is concrete.
    """
    generated = {m: expand_cascade(seed_ids, m, deps).generate_ids for m in _CASCADE_MODES}
    if len({frozenset(ids) for ids in generated.values()}) == 1:
        return default

    base = len(generated["none"])
    console.print("\n[bold]Cascade[/bold] — the pages that summarize the ones you are writing:")
    labels = {
        "none": "write exactly these pages; mark the rest stale",
        "dependents": "also rewrite the layer pages that contain them",
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
    deps: PageDependencies,
) -> InteractiveChoice | None:
    """Show the wiki state and resolve a bare ``generate`` to "write the stubs".

    Returns ``None`` when there is nothing to write (every page is already
    written). Otherwise the run writes the unwritten pages (the caller's default
    intent), and this only decides the cascade, asking when it changes the
    outcome.
    """
    print_wiki_state(console, records)
    unwritten = {r.page_id for r in _concept_records(records) if r.is_template}
    if not unwritten:
        console.print("[green]Every subsystem page is already written.[/green] Nothing to upgrade.")
        return None

    cascade_mode = choose_cascade(console, unwritten, deps)
    return InteractiveChoice(cascade_mode=cascade_mode)
