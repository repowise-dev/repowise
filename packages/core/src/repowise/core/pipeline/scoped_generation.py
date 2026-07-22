"""Portable scoped-generation service: rehydrate -> generate a subset -> persist.

The engine behind ``repowise generate`` and its server/hosted equivalents. It
writes exactly the requested subset of wiki pages (``only_page_ids``), then marks
the uncovered structural dependents stale and heals backlinks LLM-free.

Two building blocks, deliberately separate so each caller injects its own
environment (DB engine, provider, embedder/vector store, FTS, progress, cost
tracker) and keeps the cost-gate UI to itself:

- :func:`rehydrate_repo` — the shared read half: rehydrate the graph + git from
  SQL (no re-resolve, no re-blame), re-parse for ASTs + source bytes off the
  event loop, load the persisted page records, the KG artifact, and the
  file -> container-page dependency map. Returns everything scope resolution and
  generation need.
- :func:`execute_scoped_generation` — the shared write half: run generation over
  a resolved :class:`~repowise.core.generation.scope.ScopePlan`, persist the
  pages, decay the stale dependents, and heal related pages.

Scope resolution itself (intent/ranked -> ``ScopePlan``) lives in
:mod:`repowise.core.generation.scope`; a caller resolves it between these two
calls so it can gate on the estimate first. Nothing here imports CLI or server
code, so hosted reuses it as-is.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from repowise.core.generation.page_selection import PageRecord
from repowise.core.generation.scope import (
    PageDependencies,
    build_dependencies,
    load_page_records,
)

logger = structlog.get_logger(__name__)


@dataclass
class RehydratedRepo:
    """The rehydrated, re-parsed view a scoped generation runs against.

    Everything downstream (scope resolution, ranked seeds, generation, backlink
    heal) reads from this, so it is built once per run.
    """

    graph_builder: Any
    git_meta_map: dict[str, dict]
    parsed_files: list[Any]
    source_map: dict[str, bytes]
    repo_structure: Any
    records: list[PageRecord]
    kg_ctx: Any
    deps: PageDependencies
    repo_name: str


@dataclass
class ScopedGenerationResult:
    """What one scoped generation produced, for the caller's report + state."""

    generated_pages: list[Any]
    marked_stale: int


def load_kg_context(repo_path: Path) -> Any:
    """Load the persisted KG artifact for layer membership (None-safe)."""
    from repowise.core.generation.kg_context import KnowledgeGraphContext

    kg_path = repo_path / ".repowise" / "knowledge-graph.json"
    if kg_path.exists():
        return KnowledgeGraphContext(kg_path)
    return KnowledgeGraphContext(None)


async def _load_page_rows(session: Any, repo_id: str) -> list[Any]:
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


async def rehydrate_repo(
    session_factory: Any,
    repo_id: str,
    repo_path: Path,
    *,
    generation_config: Any,
    exclude_patterns: list[str],
    include_submodules: bool = False,
    include_nested_repos: bool = False,
) -> RehydratedRepo | None:
    """Rehydrate the graph + git and re-parse, ready to resolve scope + generate.

    Returns ``None`` when the repo has no persisted wiki pages yet (nothing to
    scope a generation against). The re-parse runs off the event loop so an async
    server caller does not block its loop on the tree-sitter pass.
    """
    from repowise.core.persistence import get_session
    from repowise.core.pipeline import rehydrate_graph_builder, reparse_repo
    from repowise.core.pipeline.resume.rehydrate import rehydrate_git_meta_map

    async with get_session(session_factory) as session:
        graph_builder = await rehydrate_graph_builder(session, repo_id, repo_path)
        git_meta_map = await rehydrate_git_meta_map(session, repo_id)
        records = load_page_records(await _load_page_rows(session, repo_id))

    if not records:
        return None

    parsed_files, source_map, repo_structure = await asyncio.to_thread(
        reparse_repo,
        repo_path,
        exclude_patterns,
        include_submodules=include_submodules,
        include_nested_repos=include_nested_repos,
    )

    repo_name = repo_path.name
    kg_ctx = load_kg_context(repo_path)
    # build_dependencies runs select_pages, which forces pagerank / betweenness /
    # community detection on the rehydrated graph — seconds of pure CPU on a large
    # repo. Offload it so an async server caller (e.g. the estimate endpoint) does
    # not stall its event loop. The metrics memoize on the builder, so the later
    # generation pass reuses them.
    deps = await asyncio.to_thread(
        build_dependencies,
        parsed_files=parsed_files,
        graph_builder=graph_builder,
        config=generation_config,
        kg_ctx=kg_ctx,
        records=records,
        repo_name=repo_name,
    )
    return RehydratedRepo(
        graph_builder=graph_builder,
        git_meta_map=git_meta_map,
        parsed_files=parsed_files,
        source_map=source_map,
        repo_structure=repo_structure,
        records=records,
        kg_ctx=kg_ctx,
        deps=deps,
        repo_name=repo_name,
    )


async def execute_scoped_generation(
    *,
    session_factory: Any,
    repo_id: str,
    repo_path: Path,
    rehydrated: RehydratedRepo,
    plan: Any,
    provider: Any,
    generation_config: Any,
    embedder: Any | None = None,
    vector_store: Any | None = None,
    fts: Any | None = None,
    progress: Any | None = None,
    cost_tracker: Any | None = None,
    concurrency: int = 12,
) -> ScopedGenerationResult:
    """Generate the plan's pages, persist them, decay dependents, heal backlinks.

    Injected, UI-free, and DB-agnostic: the caller supplies the session factory,
    provider, and optional embedder / vector store / FTS / progress / cost
    tracker. ``plan`` is a resolved
    :class:`~repowise.core.generation.scope.ScopePlan`.
    """
    from repowise.core.persistence import get_session, upsert_pages_from_generated
    from repowise.core.persistence.crud import backfill_related_pages
    from repowise.core.pipeline import run_generation
    from repowise.core.pipeline.persist import mark_page_ids_stale

    # Bill this run's LLM calls to the caller's tracker. run_generation reads the
    # passed tracker; some providers additionally consult ``_cost_tracker``.
    if cost_tracker is not None:
        provider._cost_tracker = cost_tracker

    generated_pages = await run_generation(
        repo_path=repo_path,
        parsed_files=rehydrated.parsed_files,
        source_map=rehydrated.source_map,
        graph_builder=rehydrated.graph_builder,
        repo_structure=rehydrated.repo_structure,
        git_meta_map=rehydrated.git_meta_map,
        llm_client=provider,
        embedder=embedder,
        vector_store=vector_store,
        concurrency=concurrency,
        progress=progress,
        cost_tracker=cost_tracker,
        generation_config=generation_config,
        only_page_ids=plan.generate_ids,
    )
    if cost_tracker is not None:
        await cost_tracker.flush()

    marked_stale = 0
    async with get_session(session_factory) as session:
        await upsert_pages_from_generated(session, generated_pages, repo_id)
        # Dependents this run did not regenerate go stale, not wrong.
        marked_stale = await mark_page_ids_stale(session, repo_id, plan.stale_ids)
        # Heal backlinks + related-pages across the whole wiki, LLM-free. The
        # pages this run just wrote already carry fresh metadata, so skip them.
        # O(total pages), same as the incremental-update path's heal; a narrow
        # scope on a huge wiki pays for the whole table. Upgrade path if that
        # ever bites: scope the heal to the regenerated pages' import
        # neighborhood. Kept whole-wiki to match the update flow and because it
        # is LLM-free (cheap next to the generation it follows).
        try:
            from repowise.core.generation.related_pages import file_import_edges

            await backfill_related_pages(
                session,
                repo_id,
                import_edges=file_import_edges(rehydrated.graph_builder),
                git_meta_map=rehydrated.git_meta_map,
                pagerank=rehydrated.graph_builder.pagerank(),
                skip_page_ids={p.page_id for p in generated_pages},
            )
        except Exception as exc:
            logger.debug("related_pages_backfill_skipped", error=str(exc))

    if fts is not None:
        try:
            for page in generated_pages:
                await fts.index(page.page_id, page.title, page.content)
        except Exception as exc:
            logger.debug("fts_index_skipped", error=str(exc))

    return ScopedGenerationResult(
        generated_pages=generated_pages,
        marked_stale=marked_stale,
    )
