"""``repowise generate`` - write the subsystem (concept) pages with a model.

The single, cost-gated entry point for turning subsystem stubs into LLM prose, or
regenerating written ones. It writes only the model-written page types (the
concept tree and the overview); a structural page id is an error. Selection is
explicit: all / unwritten / stale / path / page, unioned. A bare ``generate`` on
a terminal prints the wiki state and writes the unwritten subsystem pages; piped
/ ``--yes`` / flagged runs default to ``--unwritten``. The cascade mode decides
what happens to the pages that summarize a regenerated concept page.
"""

from __future__ import annotations

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
    resolve_max_file_pages,
    resolve_provider_or_prompt,
    resolve_reasoning,
    resolve_repo_path,
    run_async,
    save_state,
)
from repowise.cli.providers import model_run_embedder
from repowise.cli.ui import load_dotenv
from repowise.cli.ui.mode_names import provider_model
from repowise.core.cost_estimator import STRUCTURAL_PAGE_TYPES
from repowise.core.decision_lanes import set_pending_decision_lanes
from repowise.core.docs_mode import docs_mode_state_fields, resolve_docs_mode
from repowise.core.generation.page_selection import PageSelectionIntent

from ..decision_lanes import print_lane_estimate
from .engine import NO_DOCUMENTATION_MESSAGE, has_documentation, run_scoped_generation


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


def _reject_structural_page_ids(page_ids: tuple[str, ...]) -> None:
    """Error clearly when an explicit ``--page`` names a structural page.

    A ``file_page`` (or any other structural type) is rendered from the code, so
    there is nothing for a model to write. Naming one is almost always a
    misunderstanding, and a silent no-op would hide it, so it is a clear error
    pointing at the command that does refresh those pages.
    """
    bad = [pid for pid in page_ids if pid.split(":", 1)[0] in STRUCTURAL_PAGE_TYPES]
    if bad:
        joined = ", ".join(bad)
        raise click.ClickException(
            f"These pages are rendered from structure, not written by a model, so "
            f"generate has nothing to write for them: {joined}. They refresh on "
            "`repowise update`. generate writes the concept and overview pages."
        )


def _dry_run_provider(
    provider_name: str | None, model: str | None, repo_path: Path, reasoning: str | None
) -> Any | None:
    """The configured provider for a ``--dry-run``, or ``None`` when there is none.

    A dry run never prompts and never needs a key; without a provider it prints
    the plan without a price.
    """
    try:
        return resolve_provider_or_prompt(
            provider_name, model, repo_path, reasoning=reasoning, interactive=False
        )
    except click.ClickException as exc:
        # Only "nothing configured" is keyless; a broken provider is a real error.
        if getattr(exc, "reason", None) != "no_provider_configured":
            raise
        return None


def _make_gate(dry_run: bool, embedder: str | None = None):
    """Return the ``gate_cost`` callback the engine invokes before generating.

    Prints the estimate, stops on ``--dry-run``, and enforces the LLM-cost
    confirmation (reusing init's gate constants). Returns True to proceed.
    """

    def gate_cost(
        cost_plans: Any,
        provider: Any,
        repo_path: Path,
        *,
        yes: bool,
        dry_run: bool,
        decisions: Any = None,
    ) -> bool:
        from repowise.cli.commands.init_cmd.generation import cost_gate_blocks, embedding_note
        from repowise.cli.cost_gate import spend_phrase
        from repowise.cli.providers.embedders import resolve_embedding_model
        from repowise.core.cost_estimator import estimate_cost

        from ..decision_lanes import with_lane_plan

        if provider is None:
            _print_unpriced_plan(cost_plans, decisions, repo_path)
            console.print("[dim]Dry run, nothing generated.[/dim]")
            return False

        est = None
        try:
            # One estimate prices pages and decision calls together.
            est = estimate_cost(
                with_lane_plan(cost_plans, decisions),
                provider.provider_name,
                provider.model_name,
                repo_path=repo_path,
                embedder=embedder,
                embedding_model=resolve_embedding_model(embedder) if embedder else None,
            )
        except Exception as exc:
            console.print(f"[yellow]Cost estimate unavailable ({exc}).[/yellow]")

        if est is not None:
            _print_page_line(cost_plans, f" with [cyan]{provider.model_name}[/cyan]")
            print_lane_estimate(decisions, provider)
            console.print(f"{spend_phrase(est)}.")
            embedding = embedding_note(est)
            if embedding:
                console.print(f"[dim]{embedding}[/dim]")

        if dry_run:
            console.print("[dim]Dry run, nothing generated.[/dim]")
            return False

        if est is not None and cost_gate_blocks(est, yes=yes, message="  Generate at this cost?"):
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
    default=None,
    help=(
        "What to do with the overview / layer pages that summarize a regenerated "
        "concept page. Default: dependents."
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
    cascade: str | None,
    provider_name: str | None,
    model: str | None,
    reasoning: str | None,
    concurrency: int,
    dry_run: bool,
    yes: bool,
    verbose: bool,
) -> None:
    """Write the concept pages with a model. Bare `generate` writes the stubs.

    Select with --all (rewrite the prose) / --unwritten (write the concept pages
    still on a stub) / --stale / --path / --page. With no flag on a terminal,
    a bare run shows the wiki's state and writes the unwritten concept pages
    after the single cost question. --page / --path resolve only to pages a model
    writes; naming a structural (file / symbol / api / infra / cycle / layer)
    page is an error, since those refresh on `repowise update`.

    Examples:
      repowise generate                 # write the unwritten concept pages
      repowise generate --unwritten     # same, explicitly
      repowise generate --all           # rewrite the prose on every concept page
      repowise generate --path src/api  # just the concept pages under src/api
      repowise generate --page module_page:src/api --cascade none
    """
    configure_cli_logging(verbose=verbose)

    repo_path = resolve_repo_path(path)
    load_dotenv(repo_path)
    state = load_state(repo_path)
    if not state:
        raise click.ClickException(f"No index found at {repo_path}. Run `repowise init` first.")

    cfg = load_config(repo_path)

    # An explicit page id must name a page a model writes. Caught up front so a
    # `--page file_page:...` is a clear error, not a silent no-op deep in resolve.
    _reject_structural_page_ids(page_ids)

    explicit = bool(all_pages or unwritten or stale or path_globs or page_ids)

    # Bare `generate` on a terminal shows the wiki state and writes the unwritten
    # concept pages. Piped / `--yes` / flagged runs skip straight to that default.
    interactive = not explicit and not yes and sys.stdin.isatty()

    # An explicit pick pulls in the pages that summarize it (dependents); the
    # interactive path asks when it would change the outcome.
    cascade_mode = cascade if cascade is not None else "dependents"

    intent = _build_intent(
        all_pages=all_pages,
        unwritten=unwritten,
        stale=stale,
        paths=path_globs,
        page_ids=page_ids,
    )

    # Checked first: with nothing to write, a missing key is not the reason.
    if not run_async(has_documentation(repo_path)):
        console.print(NO_DOCUMENTATION_MESSAGE)
        return

    if dry_run:
        provider = _dry_run_provider(provider_name, model, repo_path, reasoning)
    else:
        # Provider is required since a model writes these pages. On a real terminal, a
        # missing one drops into init's provider + key prompt and persists the choice,
        # so a first `generate` onboards the same way init does instead of dying.
        # Onboarding needs a terminal on both ends: a terminal stdout (so the provider
        # table renders) and a tty stdin (so the answer can be read). The stdin check
        # keeps a background hook / CI / agent run from hanging even when it inherits a
        # FORCE_COLOR that makes console.is_terminal report True; those keep the clean
        # "No provider configured" error. Unlike the bare-run default, this fires
        # even with explicit page flags, so a keyless `generate --all` still onboards,
        # not just a bare `generate`.
        can_prompt_provider = console.is_terminal and sys.stdin.isatty() and not yes
        provider = resolve_provider_or_prompt(
            provider_name, model, repo_path, reasoning=reasoning, interactive=can_prompt_provider
        )

    console.print(f"[bold]repowise generate[/bold] {repo_path}")
    if provider is not None:
        console.print(
            f"Provider: [cyan]{provider_model(provider.provider_name, provider.model_name)}[/cyan]"
        )

    from repowise.core.generation import GenerationConfig

    config = GenerationConfig.from_repo_config(
        cfg,
        max_concurrency=concurrency,
        language=cfg.get("language", "en"),
        reasoning=resolve_reasoning(reasoning, cfg),
        enable_onboarding=bool(cfg.get("enable_onboarding", True)),
        wiki_style=cfg.get("wiki_style", "comprehensive"),
        # A whole-repo selection, so the file-page cap chosen at init applies
        # here; without it this run would grow the file layer back.
        max_file_pages=resolve_max_file_pages(config=cfg),
    )

    exclude_patterns = list(cfg.get("exclude_patterns") or [])

    # A keyless init records `embedder: mock`, because that mode promises no
    # spend and a hosted embedder is a real bill. The promise is void here: the
    # user is already paying a model to write these pages. Honouring the pin
    # would embed that paid prose as 8-dim SHA-256 hashes and leave semantic
    # search as useless as it was before, with nothing in the output saying so.
    # `update --full` has re-resolved here since it was written; `generate`
    # never did. Same rule, same message.
    embedder_name, stated, embedder_upgraded = model_run_embedder(cfg.get("embedder"))
    console.print(f"Embedder: [cyan]{stated}[/cyan].")

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
            gate_cost=_make_gate(dry_run, embedder_name or "mock"),
            interactive=interactive,
        )
    )

    if outcome is None:
        return  # nothing generated, dry run, or declined — messaging already printed

    _write_state(repo_path, state, provider, outcome, embedder=embedder_name)

    _report_outcome(outcome, time.monotonic() - start)
    if embedder_upgraded:
        _reembed_after_upgrade(repo_path, embedder_name)


def _account_for_plan(
    planned_ids: set[str], generated_pages: list[Any], swept_ids: list[str]
) -> dict[str, int]:
    """Sort every planned page into exactly one outcome bucket."""
    from repowise.core.generation.models import STUB_FALLBACK_ERROR

    counts = {"written": 0, "unchanged": 0, "failed": 0}
    produced: set[str] = set()
    for page in generated_pages:
        produced.add(page.page_id)
        meta = page.metadata or {}
        if STUB_FALLBACK_ERROR in meta:
            counts["failed"] += 1
        elif meta.get("reused_from_prior_run"):
            counts["unchanged"] += 1
        else:
            counts["written"] += 1
    missing = planned_ids - produced
    counts["retired"] = len(missing & set(swept_ids))
    counts["not_produced"] = len(missing) - counts["retired"]
    return counts


def _print_page_line(cost_plans: Any, detail: str) -> None:
    """``Writing N pages <detail>.``, or nothing on a decisions-only run."""
    pages = sum(p.count for p in cost_plans)
    if pages:
        console.print(f"Writing [bold]{pages}[/bold] pages{detail}.")


def _print_unpriced_plan(cost_plans: Any, decisions: Any, repo_path: Path) -> None:
    """The keyless dry run: token counts for pages and decision lanes, no price."""
    from repowise.core.cost_estimator import estimate_cost

    est = estimate_cost(cost_plans, "none", "", repo_path=repo_path)
    tokens = est.estimated_input_tokens + est.estimated_output_tokens
    _print_page_line(cost_plans, f", about [bold]{tokens:,}[/bold] tokens")
    print_lane_estimate(decisions, None)
    console.print(
        "No model is configured, so there is no price; pass [cyan]--provider[/cyan] to price it."
    )


def _report_outcome(outcome: Any, elapsed: float) -> None:
    """Report the pages, or only the cost on a decisions-only run."""
    if outcome.plan.generate_ids:
        _report_pages(outcome, elapsed)
    else:
        # The lane lines already said what the decisions-only run found.
        console.print(f"Cost: [bold]${outcome.cost_usd:.3f}[/bold] ({outcome.tokens:,} tokens).")


def _report_pages(outcome: Any, elapsed: float) -> None:
    """Print where every planned page went, the actual cost, and what is left."""
    planned = outcome.plan.generate_ids
    c = _account_for_plan(planned, outcome.generated_pages, outcome.swept_page_ids)
    done = c["written"] + c["unchanged"]
    unchanged = f" ({c['unchanged']} unchanged, no model call)" if c["unchanged"] else ""
    stale_note = f", {outcome.marked_stale} dependents marked stale" if outcome.marked_stale else ""
    console.print(
        f"[bold green]Generated {done} of {len(planned)} planned pages[/bold green]"
        f"{unchanged} in {elapsed:.1f}s{stale_note}."
    )
    rest = [
        f"{c['retired']} retired (merged into pages this run wrote)" if c["retired"] else "",
        f"{c['not_produced']} not produced (the code no longer yields them)"
        if c["not_produced"]
        else "",
        f"{c['failed']} failed (kept as a stub)" if c["failed"] else "",
    ]
    rest = [r for r in rest if r]
    if rest:
        console.print("  Not generated: " + ", ".join(rest) + ".")
    console.print(f"Cost: [bold]${outcome.cost_usd:.3f}[/bold] ({outcome.tokens:,} tokens).")

    if outcome.remaining_template_pages:
        console.print(
            f"[yellow]{outcome.remaining_template_pages} page(s) still unwritten.[/yellow] "
            "Run [cyan]repowise generate --unwritten[/cyan] to write them."
        )
    else:
        console.print("Every concept page is now written.")
    # A planned page the code no longer yields stays stale however often it is
    # retried, so only the rest are pointed at `generate --stale`.
    stuck = outcome.stale_page_ids & (planned - {p.page_id for p in outcome.generated_pages})
    stuck -= set(outcome.swept_page_ids)
    retryable = len(outcome.stale_page_ids) - len(stuck)
    if retryable:
        console.print(
            f"[yellow]{retryable} page(s) still stale.[/yellow] "
            "Run [cyan]repowise generate --stale[/cyan] to regenerate them."
        )
    if stuck:
        console.print(
            f"[dim]{len(stuck)} stale page(s) were not produced this run; "
            "regenerating will not refresh them.[/dim]"
        )


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


def _write_state(
    repo_path: Path,
    state: dict,
    provider: Any,
    outcome: Any,
    *,
    embedder: str | None = None,
) -> None:
    """Persist the post-run state: page count, provider, docs mode and scope.

    docs_mode flips to ``llm`` only once no template page remains — a partial
    upgrade leaves a mixed wiki, so it keeps its current mode rather than
    over-claiming that everything is written.
    """
    from repowise.core.index_scope import stamp_index_scope

    head = get_head_commit(repo_path)
    if head is not None:
        state["last_sync_commit"] = head
    state["total_pages"] = outcome.total_pages
    state["provider"] = provider.provider_name
    state["model"] = provider.model_name
    all_written = outcome.remaining_template_pages == 0
    if all_written and resolve_docs_mode(state) != "llm":
        state.update(docs_mode_state_fields("llm"))
    provider_block = {
        "name": provider.provider_name,
        "model": provider.model_name,
        "model_cost_possible": True,
    }
    if embedder is not None:
        provider_block["embedder"] = embedder
    # The model upgrade this index was waiting on is what this run did; the
    # pages still unwritten are named by the report, not by a pending upgrade.
    stamp_index_scope(
        state,
        load_config(repo_path),
        content_provenance="model" if all_written else "mixed",
        provider=provider_block,
        upgrade={"status": "not_applicable", "retryable": False, "next_stage": None},
    )
    lanes = getattr(outcome, "decision_lanes", None)
    if lanes is not None:
        set_pending_decision_lanes(state, lanes.still_pending)
    save_state(repo_path, state)
