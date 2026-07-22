"""``repowise generate`` — write any subset of the wiki with a model.

The single, cost-gated entry point for turning template (unwritten) pages into
LLM prose, or regenerating written ones. Selection is explicit (all / unwritten
/ stale / path / page) and defaults to ``--unwritten``; the cascade mode decides
what happens to the pages that summarize a regenerated file.
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
    "--cascade",
    type=click.Choice(["none", "dependents", "full"]),
    default="dependents",
    help="What to do with the module/overview pages that summarize a regenerated file.",
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
    cascade: str,
    provider_name: str | None,
    model: str | None,
    reasoning: str | None,
    concurrency: int,
    dry_run: bool,
    yes: bool,
    verbose: bool,
) -> None:
    """Write wiki pages with a model. Defaults to every unwritten page.

    Examples:
      repowise generate                 # write every template page
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

    config = GenerationConfig(
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
    embedder_name = cfg.get("embedder")

    start = time.monotonic()
    outcome = run_async(
        run_scoped_generation(
            repo_path,
            provider,
            config,
            intent=intent,
            cascade_mode=cascade,  # type: ignore[arg-type]
            exclude_patterns=exclude_patterns,
            embedder_name=embedder_name,
            yes=yes,
            dry_run=dry_run,
            gate_cost=_make_gate(dry_run),
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
