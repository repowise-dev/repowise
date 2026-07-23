"""``repowise generate`` — write any subset of the wiki with a model.

The single, cost-gated entry point for turning template (unwritten) pages into
LLM prose, or regenerating written ones. Selection comes in two shapes: explicit
(all / unwritten / stale / path / page, unioned) and ranked by importance
(coverage / top). A bare ``generate`` on a terminal opens an interactive chooser
(wiki state + coverage menu) instead of writing everything; piped / ``--yes`` /
flagged runs default to ``--unwritten``. The cascade mode decides what happens to
the pages that summarize a regenerated file.
"""

from __future__ import annotations

import dataclasses
import sys
import time
from pathlib import Path
from typing import Any

import click

from repowise.cli._setup import configure_cli_logging
from repowise.cli.helpers import (
    console,
    get_head_commit,
    load_config,
    load_state,
    resolve_provider,
    resolve_reasoning,
    resolve_repo_path,
    run_async,
    save_state,
)
from repowise.cli.providers import resolve_embedder
from repowise.cli.ui import load_dotenv
from repowise.core.docs_mode import docs_mode_state_fields, resolve_docs_mode
from repowise.core.generation.page_selection import PageSelectionIntent

from .engine import run_scoped_generation


def _build_intent(
    *,
    all_pages: bool,
    unwritten: bool,
    stale: bool,
    paths: tuple[str, ...],
    page_ids: tuple[str, ...],
) -> PageSelectionIntent:
    """Turn the CLI flags into an intent, defaulting to ``--unwritten``.

    No selection flag means "upgrade everything still on a template", the case
    an index-only user reaches for.
    """
    intent = PageSelectionIntent(
        all_pages=all_pages,
        unwritten=unwritten,
        stale=stale,
        path_globs=paths,
        page_ids=page_ids,
    )
    if intent.is_empty():
        return PageSelectionIntent(unwritten=True)
    return intent


def _resolve_ranked_flags(
    *, coverage: float | None, top_n: int | None, explicit: bool
) -> float | None:
    """Validate --coverage / --top and return the coverage fraction (or None).

    ``--coverage`` accepts a percent from 1 to 100 (``20`` -> 0.20, ``100`` ->
    everything) or a fraction below 1 (``0.2`` -> 0.20). The boundary is ``>= 1``
    so ``--coverage 1`` reads as 1 percent (the smallest slice), never as 100
    percent; use ``100`` or ``--all`` for everything. Ranked selection is its own
    philosophy, so it may not be combined with an explicit selector or with the
    other ranked flag. ``--top`` is handled downstream (it needs the file count),
    so this only validates it here and returns None for the coverage fraction.
    """
    if coverage is not None and top_n is not None:
        raise click.ClickException("Use either --coverage or --top, not both.")
    if (coverage is not None or top_n is not None) and explicit:
        raise click.ClickException(
            "--coverage / --top rank pages by importance and cannot be combined with "
            "--all / --unwritten / --stale / --path / --page."
        )
    if top_n is not None and top_n <= 0:
        raise click.ClickException("--top must be a positive number of pages.")
    if coverage is None:
        return None
    if coverage <= 0:
        raise click.ClickException(
            "--coverage must be positive: a percent from 1 to 100, or a fraction below 1."
        )
    # A value of 1 or more is a percent (1 -> 1%, 100 -> everything); below 1 it
    # is a fraction (0.2 -> 20%). This keeps `--coverage 1` a tiny slice, not the
    # whole wiki.
    pct = coverage / 100 if coverage >= 1 else coverage
    return min(pct, 1.0)


def _make_gate(dry_run: bool):
    """Return the ``gate_cost`` callback the engine invokes before generating.

    Prints the estimate, stops on ``--dry-run``, and enforces the LLM-cost
    confirmation (reusing init's gate constants). Returns True to proceed.
    """

    def gate_cost(
        cost_plans: Any, provider: Any, repo_path: Path, *, yes: bool, dry_run: bool
    ) -> bool:
        from repowise.cli.commands.init_cmd.generation import (
            COST_GATE_USD,
            cost_gate_declined,
            format_cost,
        )
        from repowise.core.cost_estimator import estimate_cost

        est = None
        try:
            est = estimate_cost(
                cost_plans, provider.provider_name, provider.model_name, repo_path=repo_path
            )
        except Exception as exc:
            console.print(f"[yellow]Cost estimate unavailable ({exc}).[/yellow]")

        if est is not None:
            pages = sum(p.count for p in cost_plans)
            console.print(
                f"Estimated: [bold]{pages}[/bold] pages, [bold]{format_cost(est)}[/bold]."
            )

        if dry_run:
            console.print("[dim]Dry run — nothing generated.[/dim]")
            return False

        if est is not None:
            if est.estimated_cost_usd > COST_GATE_USD and not yes and not sys.stdin.isatty():
                raise click.ClickException(
                    f"This would spend about {format_cost(est)} and there is no terminal "
                    "to confirm on. Re-run with --yes to accept the cost."
                )
            if cost_gate_declined(est, yes=yes, message="  Generate at this cost?"):
                console.print("[yellow]Aborted.[/yellow] Nothing generated.")
                return False
        return True

    return gate_cost


@click.command("generate")
@click.argument("path", required=False, default=None)
@click.option("--all", "all_pages", is_flag=True, default=False, help="Regenerate every page.")
@click.option(
    "--unwritten",
    is_flag=True,
    default=False,
    help="Write every template (unwritten) page. The default when no selection is given.",
)
@click.option("--stale", is_flag=True, default=False, help="Regenerate pages marked stale.")
@click.option(
    "--path",
    "path_globs",
    multiple=True,
    help="Restrict to pages under a path prefix or glob (repeatable).",
)
@click.option(
    "--page",
    "page_ids",
    multiple=True,
    help="An explicit page id to generate (repeatable).",
)
@click.option(
    "--coverage",
    "coverage",
    type=float,
    default=None,
    help=(
        "Write the most important unwritten pages up to this coverage, ranked the "
        "way `init` ranks coverage. A percent from 1 to 100 (--coverage 20) or a "
        "fraction below 1 (0.2); use 100 for everything. Leaves the rest as templates."
    ),
)
@click.option(
    "--top",
    "top_n",
    type=int,
    default=None,
    help="Write about the N most important unwritten pages (a target, not exact).",
)
@click.option(
    "--cascade",
    type=click.Choice(["none", "dependents", "full"]),
    default=None,
    help=(
        "What to do with the module/overview pages that summarize a regenerated "
        "file. Default: dependents for an explicit selection, none for --coverage/--top."
    ),
)
@click.option("--provider", "provider_name", default=None, help="LLM provider name.")
@click.option("--model", default=None, help="Model identifier override.")
@click.option("--reasoning", default=None, help="Reasoning mode override.")
@click.option("--concurrency", type=int, default=12, help="Max concurrent LLM calls.")
@click.option(
    "--dry-run", is_flag=True, default=False, help="Print the plan and estimate; generate nothing."
)
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip the cost confirmation.")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show pipeline debug logs.")
def generate_command(
    path: str | None,
    all_pages: bool,
    unwritten: bool,
    stale: bool,
    path_globs: tuple[str, ...],
    page_ids: tuple[str, ...],
    coverage: float | None,
    top_n: int | None,
    cascade: str | None,
    provider_name: str | None,
    model: str | None,
    reasoning: str | None,
    concurrency: int,
    dry_run: bool,
    yes: bool,
    verbose: bool,
) -> None:
    """Write wiki pages with a model. Bare `generate` opens an interactive chooser.

    Two ways to select: explicit (--all / --unwritten / --stale / --path / --page)
    or ranked by importance (--coverage / --top). With no flag on a terminal, a
    chooser shows the wiki's state and a coverage menu, so a big repo is never one
    keystroke from writing everything.

    Examples:
      repowise generate                 # interactive chooser (coverage menu)
      repowise generate --coverage 20   # write the most important 20% of templates
      repowise generate --unwritten     # write every template page
      repowise generate --all           # rewrite the whole wiki
      repowise generate --path src/api  # just the pages under src/api
      repowise generate --page file_page:src/app.py --cascade none
    """
    configure_cli_logging(verbose=verbose)

    repo_path = resolve_repo_path(path)
    load_dotenv(repo_path)
    state = load_state(repo_path)
    if not state:
        raise click.ClickException(f"No index found at {repo_path}. Run `repowise init` first.")

    cfg = load_config(repo_path)

    explicit = bool(all_pages or unwritten or stale or path_globs or page_ids)
    coverage_pct = _resolve_ranked_flags(coverage=coverage, top_n=top_n, explicit=explicit)
    ranked = coverage_pct is not None or top_n is not None

    # Bare `generate` on a terminal opens the chooser rather than defaulting to
    # writing every unwritten page. Piped / `--yes` / flagged runs keep the old
    # non-interactive behaviour.
    interactive = not explicit and not ranked and not yes and sys.stdin.isatty()

    # Cascade default depends on the selection kind: an explicit pick pulls in
    # its container pages (dependents); a ranked coverage set is already a
    # curated, coherent slice, so it defaults to none (the interactive chooser
    # asks when it would change the outcome).
    cascade_mode = cascade if cascade is not None else ("none" if ranked else "dependents")

    intent = _build_intent(
        all_pages=all_pages,
        unwritten=unwritten,
        stale=stale,
        paths=path_globs,
        page_ids=page_ids,
    )

    # Provider is required — a model writes these pages. A missing one is an
    # actionable error (resolve_provider names the key-setup path), not a trace.
    provider = resolve_provider(provider_name, model, repo_path=repo_path)

    console.print(
        f"[bold]repowise generate[/bold] — {repo_path}\n"
        f"Provider: [cyan]{provider.provider_name}[/cyan] / [cyan]{provider.model_name}[/cyan]"
    )

    from repowise.core.generation import GenerationConfig

    config = GenerationConfig.from_repo_config(
        cfg,
        max_concurrency=concurrency,
        language=cfg.get("language", "en"),
        reasoning=resolve_reasoning(reasoning, cfg),
        enable_onboarding=bool(cfg.get("enable_onboarding", True)),
        wiki_style=cfg.get("wiki_style", "comprehensive"),
    )
    tier1_top_n = cfg.get("tier1_top_n")
    if tier1_top_n is not None:
        config = dataclasses.replace(config, tier1_top_n=tier1_top_n)

    exclude_patterns = list(cfg.get("exclude_patterns") or [])

    # A keyless init records `embedder: mock`, because that mode promises no
    # spend and a hosted embedder is a real bill. The promise is void here: the
    # user is already paying a model to write these pages. Honouring the pin
    # would embed that paid prose as 8-dim SHA-256 hashes and leave semantic
    # search as useless as it was before, with nothing in the output saying so.
    # `update --full` has re-resolved here since it was written; `generate`
    # never did. Same rule, same message.
    embedder_name = cfg.get("embedder")
    embedder_upgraded = False
    if not embedder_name or embedder_name == "mock":
        resolved = resolve_embedder(None)
        if resolved != (embedder_name or "mock"):
            console.print(f"Embedder: [cyan]{resolved}[/cyan] (was mock, index-only's default).")
            embedder_name = resolved
            embedder_upgraded = True

    start = time.monotonic()
    outcome = run_async(
        run_scoped_generation(
            repo_path,
            provider,
            config,
            intent=intent,
            cascade_mode=cascade_mode,  # type: ignore[arg-type]
            exclude_patterns=exclude_patterns,
            embedder_name=embedder_name,
            yes=yes,
            dry_run=dry_run,
            gate_cost=_make_gate(dry_run),
            coverage_pct=coverage_pct,
            top_n=top_n,
            interactive=interactive,
        )
    )

    if outcome is None:
        return  # nothing generated, dry run, or declined — messaging already printed

    _write_state(repo_path, state, provider, outcome)

    elapsed = time.monotonic() - start
    tail = (
        f", {outcome.remaining_template_pages} still unwritten"
        if outcome.remaining_template_pages
        else " — every page is now written"
    )
    stale_note = f", {outcome.marked_stale} marked stale" if outcome.marked_stale else ""
    console.print(
        f"[bold green]Generated {len(outcome.generated_pages)} pages[/bold green] "
        f"in {elapsed:.1f}s{stale_note}{tail}."
    )
    if embedder_upgraded:
        _reembed_after_upgrade(repo_path, embedder_name)


def _reembed_after_upgrade(repo_path: Path, embedder_name: str) -> None:
    """Re-embed the whole wiki after the embedder was upgraded off the mock.

    Switching vector width makes the LanceDB writer drop the old table, so at
    this point the store holds only the pages this run happened to write. Every
    page it did not touch is out of semantic search, which is the exact failure
    this command's embedder fix exists to end — leaving the user a note to run
    `reindex` themselves just moves the failure one step later.

    So run it. It is the cheap half of the pipeline (embedding calls, no model)
    and it persists the resolved embedder to config.yaml on the way out, which
    is what keeps the next `update` from building a mock store against this
    table. Best-effort: the pages are written and committed either way, so an
    embedding failure is a degraded search index, not a failed generate.

    Nothing is persisted on failure. The pin is a claim about what wrote the
    table, and writing it after a run that wrote nothing makes it a false one.
    Leaving it as it was keeps the store guarded rather than trusted.
    """
    from .. import reindex_cmd

    console.print(f"\nRe-embedding the wiki with [cyan]{embedder_name}[/cyan] (no model calls)...")
    tail = "The pages are written. Run [cyan]repowise reindex[/cyan] to finish the search index."
    try:
        run_async(reindex_cmd._reindex(repo_path, embedder_name, 32))
    except click.Abort:
        # _reindex already printed the reason and its own fix; str(Abort) is
        # empty, so repeating it as "{exc}" would print a blank line instead.
        console.print(tail)
    except Exception as exc:
        console.print(f"[yellow]Re-embedding failed:[/yellow] {exc}\n{tail}")


def _write_state(repo_path: Path, state: dict, provider: Any, outcome: Any) -> None:
    """Persist the post-run state: page count, provider, and docs mode.

    docs_mode flips to ``llm`` only once no template page remains — a partial
    upgrade leaves a mixed wiki, so it keeps its current mode rather than
    over-claiming that everything is written.
    """
    state["last_sync_commit"] = get_head_commit(repo_path)
    state["total_pages"] = outcome.total_pages
    state["provider"] = provider.provider_name
    state["model"] = provider.model_name
    if outcome.remaining_template_pages == 0 and resolve_docs_mode(state) != "llm":
        state.update(docs_mode_state_fields("llm"))
    save_state(repo_path, state)
