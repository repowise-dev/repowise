"""The ``repowise generate`` engine: rehydrate -> scope -> gate -> generate.

Mirrors the upgrade/restyle flows (rehydrate the graph and git from SQL,
re-parse for ASTs, run generation, persist + FTS + embed) but drives the
generation through ``only_page_ids`` so it writes exactly the requested subset,
then marks the uncovered dependents stale and heals backlinks LLM-free.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repowise.cli.helpers import console
from repowise.core.generation.cascade import CascadeMode
from repowise.core.generation.page_selection import PageSelectionIntent

from .scope import (
    ScopePlan,
    build_dependencies,
    build_ranked_seed,
    load_page_records,
    resolve_scope,
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
    from repowise.cli.commands.upgrade_flow import _reparse
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.cli.providers import (
        build_embedder,
        build_vector_store,
        cost_tracking_disabled,
        resolve_embedder,
    )
    from repowise.core.generation.cost_tracker import CostTracker
    from repowise.core.generation.kg_context import KnowledgeGraphContext
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_pages_from_generated,
        upsert_repository,
    )
    from repowise.core.persistence.crud import backfill_related_pages
    from repowise.core.pipeline import rehydrate_graph_builder, run_generation
    from repowise.core.pipeline.persist import mark_page_ids_stale
    from repowise.core.pipeline.resume.rehydrate import rehydrate_git_meta_map

    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    await init_db(engine)
    sf = create_session_factory(engine)

    try:
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
            repo_id = repo.id

        # Rehydrate the graph + git signals from SQL — no re-resolve, no re-blame.
        async with get_session(sf) as session:
            graph_builder = await rehydrate_graph_builder(session, repo_id, repo_path)
            git_meta_map = await rehydrate_git_meta_map(session, repo_id)
            records = load_page_records(await _load_pages(session, repo_id))

        if not records:
            console.print(
                "[yellow]This repo has no wiki pages yet.[/yellow] "
                "Run `repowise init --index-only` or `repowise update --full` first."
            )
            return None

        parsed_files, source_map, repo_structure = _reparse(repo_path, exclude_patterns)
        console.print(
            f"Re-parsed [cyan]{len(parsed_files)}[/cyan] files (graph + git reused from index)."
        )

        repo_name = repo_path.name
        kg_ctx = _load_kg_ctx(repo_path, KnowledgeGraphContext)
        deps = build_dependencies(
            parsed_files=parsed_files,
            graph_builder=graph_builder,
            config=config,
            kg_ctx=kg_ctx,
            records=records,
            repo_name=repo_name,
        )
        # Ranked (coverage/top) and interactive runs replace the intent seed
        # with an importance-ranked page-id set; explicit runs leave it None.
        ranked_seed: set[str] | None = None
        if interactive:
            from .chooser import run_interactive_chooser

            choice = run_interactive_chooser(
                console,
                records=records,
                parsed_files=parsed_files,
                graph_builder=graph_builder,
                config=config,
                kg_ctx=kg_ctx,
                provider=provider,
                repo_path=repo_path,
                repo_name=repo_name,
                deps=deps,
            )
            if choice is None:
                return None
            ranked_seed = choice.ranked_seed
            cascade_mode = choice.cascade_mode
        elif coverage_pct is not None or top_n is not None:
            pct = coverage_pct
            if pct is None:
                pct = _coverage_from_top(top_n or 0, len(parsed_files))
            ranked_seed = build_ranked_seed(
                parsed_files=parsed_files,
                graph_builder=graph_builder,
                config=config,
                kg_ctx=kg_ctx,
                records=records,
                repo_name=repo_name,
                coverage_pct=pct,
            )
            if not ranked_seed:
                console.print(
                    "[yellow]Everything in that coverage is already written.[/yellow] "
                    "Nothing to generate."
                )
                return None

        plan = resolve_scope(
            records=records,
            intent=intent,
            cascade_mode=cascade_mode,
            deps=deps,
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
        provider._cost_tracker = cost_tracker

        embedder = None
        vector_store = None
        try:
            embedder = build_embedder(resolve_embedder(embedder_name))
            vector_store = build_vector_store(repo_path, embedder)
        except Exception as exc:
            # Null BOTH on any failure. If the embedder built but the store did
            # not, a non-None embedder would make run_generation embed into a
            # throwaway in-memory store — pages silently absent from semantic
            # search while we claimed embedding was skipped.
            embedder = None
            vector_store = None
            console.print(f"[yellow]Embedding skipped: {exc}[/yellow]")

        generated_pages = await run_generation(
            repo_path=repo_path,
            parsed_files=parsed_files,
            source_map=source_map,
            graph_builder=graph_builder,
            repo_structure=repo_structure,
            git_meta_map=git_meta_map,
            llm_client=provider,
            embedder=embedder,
            vector_store=vector_store,
            concurrency=config.max_concurrency,
            progress=None,
            cost_tracker=cost_tracker,
            generation_config=config,
            only_page_ids=plan.generate_ids,
        )
        await cost_tracker.flush()

        marked_stale = 0
        async with get_session(sf) as session:
            await upsert_pages_from_generated(session, generated_pages, repo_id)
            # Dependents this run did not regenerate go stale, not wrong.
            marked_stale = await mark_page_ids_stale(session, repo_id, plan.stale_ids)
            # Heal backlinks + related-pages across the whole wiki, LLM-free. The
            # pages this run just wrote already carry fresh metadata, so skip them.
            # This is O(total pages), same as the incremental-update path's heal;
            # a narrow scope on a huge wiki pays for the whole table. Upgrade path
            # if that ever bites: scope the heal to the regenerated pages' import
            # neighborhood. Kept whole-wiki for now to match the update flow and
            # because it is LLM-free (cheap next to the generation it follows).
            try:
                from repowise.core.generation.related_pages import file_import_edges

                await backfill_related_pages(
                    session,
                    repo_id,
                    import_edges=file_import_edges(graph_builder),
                    git_meta_map=git_meta_map,
                    pagerank=graph_builder.pagerank(),
                    skip_page_ids={p.page_id for p in generated_pages},
                )
            except Exception as exc:
                console.print(f"[dim]Related-pages backfill skipped: {exc}[/dim]")

        await _index_fts(engine, generated_pages)

        total_pages, remaining_templates = await _page_stats(sf, repo_id)
        return GenerateOutcome(
            generated_pages=generated_pages,
            total_pages=total_pages,
            marked_stale=marked_stale,
            remaining_template_pages=remaining_templates,
            plan=plan,
        )
    finally:
        await engine.dispose()


async def _load_pages(session: Any, repo_id: str) -> list[Any]:
    """Every non-tombstoned persisted page, lightweight columns only.

    ``load_page_records`` reads just id/type/target/provider/metadata/freshness,
    so ``load_only`` keeps the (potentially very large) ``content`` Text column
    out of this whole-wiki scan.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import load_only

    from repowise.core.persistence.models import Page

    result = await session.execute(
        select(Page)
        .options(
            load_only(
                Page.id,
                Page.page_type,
                Page.target_path,
                Page.provider_name,
                Page.metadata_json,
                Page.freshness_status,
            )
        )
        .where(
            Page.repository_id == repo_id,
            Page.freshness_status != "tombstone",
        )
    )
    return list(result.scalars())


def _load_kg_ctx(repo_path: Path, kg_ctx_cls: Any) -> Any:
    """Load the persisted KG artifact for layer membership (None-safe)."""
    kg_path = repo_path / ".repowise" / "knowledge-graph.json"
    if kg_path.exists():
        return kg_ctx_cls(kg_path)
    return kg_ctx_cls(None)


async def _index_fts(engine: Any, generated_pages: list[Any]) -> None:
    """Best-effort full-text (re)index of the freshly generated pages."""
    try:
        from repowise.core.persistence import FullTextSearch

        fts = FullTextSearch(engine)
        await fts.ensure_index()
        for page in generated_pages:
            await fts.index(page.page_id, page.title, page.content)
    except Exception:
        pass  # FTS indexing is best-effort


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
