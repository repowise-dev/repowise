"""Refresh a template wiki on an index-only update.

``repowise init --index-only`` renders a complete wiki from the repo's
structure. Without this module that wiki would be frozen at the commit it was
built from: an index-only update refreshes the graph, git history, health and
dead code, but never touched a page, because before templates existed there
were no pages on that path to touch.

Re-rendering costs nothing (no provider, no tokens), so the changed files'
pages are re-rendered on every update, the same set the LLM path would
regenerate. The work is deliberately narrow compared with
``_persist_full_update``: no decision extraction, no decision evolution, no KG
enrichment. All three are prompting, and this path has no model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repowise.cli.helpers import console, run_async


def deterministic_embedder_name(cfg: dict) -> str:
    """Resolve the embedder for a no-spend run, honouring only an explicit one.

    Same rule as ``init --index-only``: a hosted embedder is a real bill, and
    ``resolve_embedder`` will infer one from any LLM key that happens to be in
    the environment. On a run sold as costing nothing, only a name the user
    actually chose counts, and index-only init persists that choice to
    ``.repowise/config.yaml``. Everything else falls back to the mock, which
    leaves full-text search working.
    """
    from repowise.cli.providers import resolve_embedder

    chosen = cfg.get("embedder")
    if chosen:
        return str(chosen)
    resolved = resolve_embedder(None)
    return resolved if resolved in ("mock", "ollama") else "mock"


def regenerate_deterministic_pages(
    *,
    repo_path: Path,
    parsed_files: list,
    source_map: dict,
    graph_builder: Any,
    repo_structure: Any,
    git_meta_map: dict,
    regenerate_paths: list[str],
    cfg: dict,
    concurrency: int,
    degraded: list[str],
) -> list:
    """Re-render the template pages for *regenerate_paths*. Never raises.

    A failure here degrades the run rather than failing it: the index half of
    an index-only update is the half the post-commit hook depends on, and it
    has already been computed by the time this runs.
    """
    from repowise.core.generation import ContextAssembler, GenerationConfig, PageGenerator
    from repowise.core.providers.llm.template import TemplateProvider

    regen_set = set(regenerate_paths)
    affected_parsed = [pf for pf in parsed_files if pf.file_info.path in regen_set]
    affected_source = {p: s for p, s in source_map.items() if p in regen_set}
    if not affected_parsed:
        return []

    try:
        config = GenerationConfig(
            deterministic=True,
            max_concurrency=concurrency,
            language=cfg.get("language", "en"),
            enable_onboarding=bool(cfg.get("enable_onboarding", True)),
            wiki_style=cfg.get("wiki_style", "comprehensive"),
        )
        from repowise.cli.providers import build_embedder, build_vector_store

        vector_store = None
        try:
            vector_store = build_vector_store(
                repo_path, build_embedder(deterministic_embedder_name(cfg))
            )
        except Exception as exc:  # embedding is optional; FTS still indexes
            degraded.append(f"Page embedding: {exc}")

        generator = PageGenerator(
            TemplateProvider(),
            ContextAssembler(config),
            config,
            vector_store=vector_store,
            language=config.language,
            # No prior-page reuse gate: template pages persist an empty
            # content_hash precisely so a later model run cannot inherit their
            # content, which also means there is nothing here to match against.
            prior_pages={},
            repo_path=repo_path,
        )
        with console.status("  Re-rendering wiki pages from structure…"):
            return run_async(
                generator.generate_all(
                    affected_parsed,
                    affected_source,
                    graph_builder,
                    repo_structure,
                    repo_path.name,
                    git_meta_map=git_meta_map,
                    repo_path=repo_path,
                )
            )
    except Exception as exc:
        degraded.append(f"Template page refresh: {exc}")
        return []


def persist_deterministic_pages(
    *,
    repo_path: Path,
    generated_pages: list,
    graph_builder: Any,
    git_meta_map: dict,
    decay_paths: list[str],
    degraded: list[str],
) -> int:
    """Write the re-rendered pages, then heal links and decay the rest.

    Returns the repository's total page count, so the caller can stamp
    ``state["total_pages"]`` with the real DB number rather than accumulating.
    Runs in its own session after ``persist_incremental_index`` rather than
    inside it: page upserts are idempotent, so the worst case of a crash
    between the two is a re-run, not a torn index.
    """
    return run_async(
        _persist_async(
            repo_path=repo_path,
            generated_pages=generated_pages,
            graph_builder=graph_builder,
            git_meta_map=git_meta_map,
            decay_paths=decay_paths,
            degraded=degraded,
        )
    )


async def _persist_async(
    *,
    repo_path: Path,
    generated_pages: list,
    graph_builder: Any,
    git_meta_map: dict,
    decay_paths: list[str],
    degraded: list[str],
) -> int:
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import (
        FullTextSearch,
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_pages_from_generated,
        upsert_repository,
    )

    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    total = 0
    try:
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
            repo_id = repo.id
            await upsert_pages_from_generated(session, generated_pages, repo_id)

            try:
                from repowise.core.generation.related_pages import file_import_edges
                from repowise.core.persistence.crud import backfill_related_pages

                await backfill_related_pages(
                    session,
                    repo_id,
                    import_edges=file_import_edges(graph_builder),
                    git_meta_map=git_meta_map,
                    pagerank=graph_builder.pagerank(),
                    skip_page_ids={p.page_id for p in generated_pages},
                )
            except Exception as exc:
                degraded.append(f"Related-pages backfill: {exc}")

            # Pages the cascade reached but the budget did not: marked stale so
            # the coverage view is honest about which template pages predate
            # the current commit.
            try:
                from repowise.core.pipeline.persist import mark_stale_pages

                await mark_stale_pages(session, repo_id, decay_paths or [])
            except Exception as exc:
                degraded.append(f"Stale-page decay: {exc}")

            # Real DB total, not an accumulation: regeneration upserts, so
            # adding len(generated_pages) each run inflates the count forever.
            total = len(generated_pages)
            try:
                from sqlalchemy import func as sa_func
                from sqlalchemy import select as sa_select

                from repowise.core.persistence.models import Page

                count_result = await session.execute(
                    sa_select(sa_func.count())
                    .select_from(Page)
                    .where(Page.repository_id == repo_id)
                )
                total = int(count_result.scalar_one())
            except Exception as exc:
                degraded.append(f"Page count: {exc}")

        try:
            fts = FullTextSearch(engine)
            await fts.ensure_index()
            for page in generated_pages:
                await fts.index(page.page_id, page.title, page.content)
        except Exception as exc:
            degraded.append(f"Full-text index: {exc}")
    finally:
        await engine.dispose()
    return total
