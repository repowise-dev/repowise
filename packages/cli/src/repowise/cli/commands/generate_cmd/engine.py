"""The ``repowise generate`` engine (CLI wrapper).

Composes the portable core service
(:mod:`repowise.core.pipeline.scoped_generation`) with the CLI's environment:
its own DB engine, the interactive chooser / ranked-coverage seed, the cost gate,
and the terminal reporting. The rehydrate + generate + persist + heal half lives
in core so the OSS server and hosted share it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repowise.cli.helpers import console, load_state
from repowise.core.generation.cascade import CascadeMode
from repowise.core.generation.page_selection import PageSelectionIntent
from repowise.core.generation.scope import ScopePlan, build_ranked_seed, resolve_scope
from repowise.core.pipeline.scoped_generation import (
    execute_scoped_generation,
    rehydrate_repo,
)


@dataclass
class GenerateOutcome:
    """What a generate run produced, for the command's state write + report."""

    generated_pages: list[Any]
    total_pages: int
    marked_stale: int
    remaining_template_pages: int
    plan: ScopePlan


def _coverage_from_top(top_n: int, n_files: int) -> float:
    """Map ``--top N`` to the coverage fraction that budgets ~N pages.

    ``compute_budget`` uses ``int(n_files * pct)`` for a large repo, so
    ``pct = N / n_files`` yields a budget of about ``N``. Per-bucket floors and
    the always-emitted repo-wide/onboarding pages nudge the actual count, which
    is why the plan report (and the cost gate) show the real number before any
    spend — ``--top`` is a target, not an exact count.
    """
    return min(1.0, max(0.0, top_n / max(1, n_files)))


async def run_scoped_generation(
    repo_path: Path,
    provider: Any,
    config: Any,
    *,
    intent: PageSelectionIntent,
    cascade_mode: CascadeMode,
    exclude_patterns: list[str],
    embedder_name: str | None,
    yes: bool,
    dry_run: bool,
    gate_cost: Any,
    coverage_pct: float | None = None,
    top_n: int | None = None,
    interactive: bool = False,
) -> GenerateOutcome | None:
    """Resolve the scope, gate on cost, generate, persist, and heal.

    Returns ``None`` when there was nothing to do (empty scope) or the run was a
    ``--dry-run``. ``gate_cost`` is injected (the upgrade flow's estimator +
    confirm) so this module stays free of the cost-gate UI.
    """
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.cli.providers import (
        build_embedder,
        build_vector_store,
        cost_tracking_disabled,
        resolve_embedder,
    )
    from repowise.core.generation.cost_tracker import CostTracker
    from repowise.core.persistence import (
        FullTextSearch,
        create_engine,
        create_session_factory,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence import get_session as _get_session

    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    await init_db(engine)
    sf = create_session_factory(engine)

    try:
        async with _get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
            repo_id = repo.id

        # Honor the persisted submodule/nested-repo semantics of the original
        # index so the docs re-parse covers the same file set init did.
        state = load_state(repo_path)
        rehydrated = await rehydrate_repo(
            sf,
            repo_id,
            repo_path,
            generation_config=config,
            exclude_patterns=exclude_patterns,
            include_submodules=bool(state.get("include_submodules", False)),
            include_nested_repos=bool(state.get("include_nested_repos", False)),
        )
        if rehydrated is None:
            console.print(
                "[yellow]This repo has no wiki pages yet.[/yellow] "
                "Run `repowise init --index-only` or `repowise update --full` first."
            )
            return None

        console.print(
            f"Re-parsed [cyan]{len(rehydrated.parsed_files)}[/cyan] files "
            "(graph + git reused from index)."
        )

        # Ranked (coverage/top) and interactive runs replace the intent seed
        # with an importance-ranked page-id set; explicit runs leave it None.
        ranked_seed: set[str] | None = None
        if interactive:
            from .chooser import run_interactive_chooser

            choice = run_interactive_chooser(
                console,
                records=rehydrated.records,
                parsed_files=rehydrated.parsed_files,
                graph_builder=rehydrated.graph_builder,
                config=config,
                kg_ctx=rehydrated.kg_ctx,
                provider=provider,
                repo_path=repo_path,
                repo_name=rehydrated.repo_name,
                deps=rehydrated.deps,
            )
            if choice is None:
                return None
            ranked_seed = choice.ranked_seed
            cascade_mode = choice.cascade_mode
        elif coverage_pct is not None or top_n is not None:
            pct = coverage_pct
            if pct is None:
                pct = _coverage_from_top(top_n or 0, len(rehydrated.parsed_files))
            ranked_seed = build_ranked_seed(
                parsed_files=rehydrated.parsed_files,
                graph_builder=rehydrated.graph_builder,
                config=config,
                kg_ctx=rehydrated.kg_ctx,
                records=rehydrated.records,
                repo_name=rehydrated.repo_name,
                coverage_pct=pct,
            )
            if not ranked_seed:
                console.print(
                    "[yellow]Everything in that coverage is already written.[/yellow] "
                    "Nothing to generate."
                )
                return None

        plan = resolve_scope(
            records=rehydrated.records,
            intent=intent,
            cascade_mode=cascade_mode,
            deps=rehydrated.deps,
            ranked_seed=ranked_seed,
        )

        if plan.unknown_page_ids:
            console.print(
                "[yellow]No such page(s), skipped:[/yellow] " + ", ".join(plan.unknown_page_ids)
            )
        if not plan.generate_ids:
            console.print("[yellow]Nothing to generate for that selection.[/yellow]")
            return None

        _report_plan(plan, cascade_mode)

        # Cost gate (and the dry-run stop) share the same estimate.
        proceed = gate_cost(plan.cost_plans, provider, repo_path, yes=yes, dry_run=dry_run)
        if not proceed:
            return None

        # Construct the tracker directly (we are already in the event loop, so
        # the run_async-based build_cost_tracker helper would fail here).
        # buffered=True defers cost INSERTs to a single post-generation flush.
        if cost_tracking_disabled():
            cost_tracker = CostTracker()
        else:
            cost_tracker = CostTracker(session_factory=sf, repo_id=repo_id, buffered=True)

        embedder = None
        vector_store = None
        try:
            embedder = build_embedder(resolve_embedder(embedder_name))
            vector_store = build_vector_store(repo_path, embedder)
        except Exception as exc:
            # Null BOTH on any failure. If the embedder built but the store did
            # not, a non-None embedder would make generation embed into a
            # throwaway in-memory store — pages silently absent from semantic
            # search while we claimed embedding was skipped.
            embedder = None
            vector_store = None
            console.print(f"[yellow]Embedding skipped: {exc}[/yellow]")

        # Best-effort: a failure to open the FTS index must not abort a run whose
        # pages are already generated. Nulling fts skips indexing (as the old
        # blanket-guarded _index_fts did) rather than raising.
        fts: Any | None = FullTextSearch(engine)
        try:
            await fts.ensure_index()
        except Exception as exc:
            console.print(f"[dim]Full-text index unavailable: {exc}[/dim]")
            fts = None

        result = await execute_scoped_generation(
            session_factory=sf,
            repo_id=repo_id,
            repo_path=repo_path,
            rehydrated=rehydrated,
            plan=plan,
            provider=provider,
            generation_config=config,
            embedder=embedder,
            vector_store=vector_store,
            fts=fts,
            progress=None,
            cost_tracker=cost_tracker,
            concurrency=config.max_concurrency,
        )

        total_pages, remaining_templates = await _page_stats(sf, repo_id)
        return GenerateOutcome(
            generated_pages=result.generated_pages,
            total_pages=total_pages,
            marked_stale=result.marked_stale,
            remaining_template_pages=remaining_templates,
            plan=plan,
        )
    finally:
        await engine.dispose()


async def _page_stats(sf: Any, repo_id: str) -> tuple[int, int]:
    """Return ``(total_pages, remaining_template_pages)`` from the DB."""
    from sqlalchemy import func as sa_func
    from sqlalchemy import select as sa_select

    from repowise.core.persistence import get_session
    from repowise.core.persistence.models import Page

    async with get_session(sf) as session:
        total = int(
            (
                await session.execute(
                    sa_select(sa_func.count())
                    .select_from(Page)
                    .where(Page.repository_id == repo_id)
                )
            ).scalar_one()
        )
        templates = int(
            (
                await session.execute(
                    sa_select(sa_func.count())
                    .select_from(Page)
                    .where(
                        Page.repository_id == repo_id,
                        Page.provider_name == "template",
                    )
                )
            ).scalar_one()
        )
    return total, templates


def _report_plan(plan: ScopePlan, cascade_mode: CascadeMode) -> None:
    """Print the resolved plan (counts by type + cascade summary)."""
    by_type: dict[str, int] = {}
    for p in plan.cost_plans:
        by_type[p.page_type] = p.count
    breakdown = ", ".join(f"{n} {t}" for t, n in by_type.items())
    console.print(
        f"Plan: [bold]{sum(by_type.values())}[/bold] pages "
        f"({breakdown or 'none'})  ·  cascade: [cyan]{cascade_mode}[/cyan]"
    )
    if plan.stale_ids:
        console.print(f"  {len(plan.stale_ids)} dependent page(s) will be marked stale.")
