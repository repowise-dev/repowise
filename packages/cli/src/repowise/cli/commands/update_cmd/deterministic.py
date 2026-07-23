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

import os
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

    chosen = cfg.get("embedder") or os.environ.get("REPOWISE_EMBEDDER", "").strip()
    if chosen:
        return str(chosen)
    # Nothing was chosen, so whatever resolve_embedder returns was inferred
    # from a key. Only the ones that cannot bill survive that.
    resolved = resolve_embedder(None)
    return resolved if resolved in ("mock", "ollama") else "mock"


def load_prior_page_ids(repo_path: Path) -> dict:
    """Every page id already in the wiki, mapped to a placeholder.

    Interlinking and related-pages resolve references against this, so a
    re-rendered page can still link to the pages this run did not touch.
    Only the ids are read: the full ``load_prior_pages`` pulls every page's
    content, and nothing here needs the bodies since template pages never go
    through the content-reuse gate.
    """
    return run_async(_load_prior_page_ids(repo_path))


async def _load_prior_page_ids(repo_path: Path) -> dict:
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import create_engine, create_session_factory, get_session

    engine = create_engine(get_db_url_for_repo(repo_path))
    try:
        from sqlalchemy import select as sa_select

        from repowise.core.persistence.models import Page

        async with get_session(create_session_factory(engine)) as session:
            rows = await session.execute(sa_select(Page.id))
            return dict.fromkeys((r[0] for r in rows), None)
    except Exception:
        return {}
    finally:
        await engine.dispose()


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
    dead_code_report: Any = None,
    prior_page_ids: dict | None = None,
) -> list:
    """Re-render the template pages for *regenerate_paths*. Never raises.

    Only the changed files' own pages. The repo-wide pages (cycles, modules,
    layers, overview, architecture diagram, onboarding) describe the whole
    repository and would be rendered here from a view containing only the
    changed files, so they are left as the last full run wrote them.

    A failure here degrades the run rather than failing it: the index half of
    an index-only update is the half the post-commit hook depends on, and it
    has already been computed by the time this runs.
    """
    return _render_pages(
        repo_path=repo_path,
        parsed_files=parsed_files,
        source_map=source_map,
        graph_builder=graph_builder,
        repo_structure=repo_structure,
        git_meta_map=git_meta_map,
        regenerate_paths=regenerate_paths,
        cfg=cfg,
        concurrency=concurrency,
        degraded=degraded,
        dead_code_report=dead_code_report,
        prior_page_ids=prior_page_ids,
        provider=None,
        degrade_label="Template page refresh",
    )


def _render_pages(
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
    dead_code_report: Any,
    prior_page_ids: dict | None,
    degrade_label: str,
) -> list:
    """Render the changed files' pages from structure (free, no LLM).

    Every file page is structural now, so there is one render mode: the template
    renderer, driven by ``deterministic=True``. There is no model path to choose.
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
            file_pages_only=True,
            max_concurrency=concurrency,
            language=cfg.get("language", "en"),
            enable_onboarding=bool(cfg.get("enable_onboarding", True)),
            wiki_style=cfg.get("wiki_style", "comprehensive"),
        )

        # Only build a store when there is a real embedder to build it with.
        # The mock is the index-only default, and writing its 8-wide vectors
        # into a store some earlier run filled at 1536 makes LanceDB drop the
        # table, taking every page and decision embedding with it. Skipping
        # leaves that store untouched; full-text search is unaffected either
        # way. It also keeps the lancedb import off the post-commit hook's
        # path, which is the one place in this command that avoids it.
        vector_store = None
        embedder_name = deterministic_embedder_name(cfg)
        if embedder_name != "mock":
            from repowise.cli.providers import build_embedder, build_vector_store

            try:
                vector_store = build_vector_store(repo_path, build_embedder(embedder_name))
            except Exception as exc:  # embedding is optional; FTS still indexes
                degraded.append(f"Page embedding: {exc}")

        generator = PageGenerator(
            provider if provider is not None else TemplateProvider(),
            ContextAssembler(config),
            config,
            vector_store=vector_store,
            language=config.language,
            # Every persisted page id, so interlinking and related-pages can
            # resolve references to pages outside this run's slice. Not a reuse
            # gate: template pages persist an empty content_hash precisely so a
            # later model run cannot inherit their content.
            prior_pages=prior_page_ids or {},
            repo_path=repo_path,
        )
        # A model render targets exactly the requested file pages so the coverage
        # budget can't ration them; a template render takes every page it is fed
        # (deterministic mode bypasses the budget already).
        only_page_ids = None if is_template else {f"file_page:{p}" for p in regenerate_paths}
        status_msg = (
            "  Re-rendering wiki pages from structure…"
            if is_template
            else "  Re-writing upgraded pages with the model…"
        )
        with console.status(status_msg):
            return run_async(
                generator.generate_all(
                    affected_parsed,
                    affected_source,
                    graph_builder,
                    repo_structure,
                    repo_path.name,
                    git_meta_map=git_meta_map,
                    repo_path=repo_path,
                    dead_code_report=dead_code_report,
                    only_page_ids=only_page_ids,
                )
            )
    except Exception as exc:
        degraded.append(f"{degrade_label}: {exc}")
        return []


def persist_deterministic_pages(
    *,
    repo_path: Path,
    generated_pages: list,
    decay_paths: list[str],
    degraded: list[str],
) -> int:
    """Write the re-rendered pages, decay the rest, and index them for search.

    Returns the repository's total page count, so the caller can stamp
    ``state["total_pages"]`` with the real DB number rather than accumulating.
    Runs before ``persist_incremental_index`` and in its own session: the pages
    must land before that call tombstones the ones whose files are gone, and
    page upserts are idempotent, so an interrupted run re-runs rather than
    tearing the index.
    """
    return run_async(
        _persist_async(
            repo_path=repo_path,
            generated_pages=generated_pages,
            decay_paths=decay_paths,
            degraded=degraded,
        )
    )


async def _persist_async(
    *,
    repo_path: Path,
    generated_pages: list,
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

            # No related-pages backfill here. ``persist_incremental_index``
            # runs one repo-wide immediately after this, unskipped, so it
            # already heals these pages; doing it first and exempting them
            # would skip the only pages that need it.

            # Pages the cascade reached but the budget did not: marked stale so
            # the coverage view is honest about which template pages predate
            # the current commit.
            try:
                from repowise.core.pipeline.persist import mark_stale_pages

                await mark_stale_pages(session, repo_id, decay_paths or [])
            except Exception as exc:
                degraded.append(f"Stale-page decay: {exc}")

            # Placement depends on the whole page set, which on an incremental
            # run lives in the store rather than in the pages just generated.
            try:
                from repowise.core.pipeline.page_tree_sync import rebuild_page_tree

                await rebuild_page_tree(session, repo_id)
            except Exception as exc:
                degraded.append(f"Page tree rebuild: {exc}")

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


def load_file_page_render_keys(repo_path: Path) -> dict[str, str]:
    """``page_id -> render key`` for every file page already in the wiki.

    A row with no key is kept rather than skipped: that is what a page written
    before the renderer carried a fingerprint looks like, and it is exactly the
    population the first run after that change has to refresh.
    """
    return run_async(_load_file_page_render_keys(repo_path))


async def _load_file_page_render_keys(repo_path: Path) -> dict[str, str]:
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import create_engine, create_session_factory, get_session

    engine = create_engine(get_db_url_for_repo(repo_path))
    try:
        import json

        from sqlalchemy import select as sa_select

        from repowise.core.generation.page_generator.structural import RENDER_KEY
        from repowise.core.persistence.models import Page

        async with get_session(create_session_factory(engine)) as session:
            rows = await session.execute(
                sa_select(Page.id, Page.metadata_json).where(Page.page_type == "file_page")
            )
            keys: dict[str, str] = {}
            for pid, meta_json in rows:
                try:
                    meta = json.loads(meta_json or "{}")
                except (TypeError, ValueError):
                    meta = {}
                keys[pid] = str(meta.get(RENDER_KEY) or "")
            return keys
    except Exception:
        # Never block an update on this. Returning nothing means no page looks
        # stale, so the run does what it would have done before the salt.
        return {}
    finally:
        await engine.dispose()
