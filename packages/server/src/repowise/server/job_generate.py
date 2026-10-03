"""Scoped generation jobs: `repowise generate` over HTTP.

Resolves a page selection (explicit ids, a path prefix, stale or unwritten
pages, or a ranked coverage slice) into a scope plan and writes exactly those
pages through the shared core engine. The estimate endpoint resolves through
the same helpers, so a job writes what its estimate priced.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from repowise.core.persistence.database import get_session
from repowise.server.job_records import _complete_job_row
from repowise.server.job_state import _load_state, _persist_generate_job_state

if TYPE_CHECKING:
    from repowise.server.job_executor import JobProgressCallback

logger = structlog.get_logger(__name__)


def _build_generate_intent(config: dict) -> Any:
    """Turn a generate job's config into a :class:`PageSelectionIntent`.

    Understands the new ``selection`` block (all / unwritten / stale / page_ids /
    path_prefix) and the legacy ``single_page`` shape (``page_id``). Defaults to
    ``unwritten`` — the index-only upgrade case — when nothing is specified.
    """
    from repowise.core.generation.page_selection import PageSelectionIntent

    if config.get("mode") == "single_page":
        pid = config.get("page_id")
        return PageSelectionIntent(page_ids=(pid,) if pid else ())

    sel = config.get("selection") or {}
    kind = sel.get("kind", "unwritten")
    if kind == "all":
        return PageSelectionIntent(all_pages=True)
    if kind == "stale":
        return PageSelectionIntent(stale=True)
    if kind == "page_ids":
        return PageSelectionIntent(page_ids=tuple(sel.get("page_ids") or ()))
    if kind == "path_prefix":
        prefix = sel.get("path_prefix")
        return PageSelectionIntent(path_globs=(prefix,) if prefix else ())
    if kind == "ranked":
        # The seed comes from build_ranked_seed and short-circuits resolve_scope,
        # so the intent is unused; return an empty one rather than picking a kind.
        return PageSelectionIntent()
    return PageSelectionIntent(unwritten=True)


def _effective_cascade(config: dict) -> str:
    """Resolve the cascade mode, honoring the same defaults the CLI uses.

    An explicit ``cascade`` in the config always wins. Left unset, a ranked
    coverage selection defaults to ``none`` (the ranked set is already a coherent
    slice) and the legacy single-page alias to ``none``; everything else defaults
    to ``dependents`` (an explicit selection pulls in its container pages).
    """
    explicit = config.get("cascade")
    if explicit:
        return explicit
    sel = config.get("selection") or {}
    if sel.get("kind") == "ranked" or config.get("mode") == "single_page":
        return "none"
    return "dependents"


def _ranked_coverage_pct(selection: dict, n_files: int) -> float:
    """The coverage fraction a ranked selection resolves to.

    ``coverage_pct`` is used verbatim (a fraction in ``(0, 1]``); ``top_n`` maps
    to ``n / n_files`` so the budget picks about N pages. ``top_n`` is a target,
    not an exact count — per-type floors and always-emitted repo-wide/onboarding
    pages nudge the real number, which the estimate and job report faithfully.
    """
    pct = selection.get("coverage_pct")
    if pct is not None:
        return float(pct)
    top_n = int(selection.get("top_n") or 0)
    return min(1.0, max(0.0, top_n / max(1, n_files)))


def _narrow_plan_to_model_written(plan: Any) -> Any:
    """Drop structural pages from a resolved plan so generate writes only prose.

    A structural page has no model to write it, so including it would only
    re-render a template and inflate the count. Mirrors the CLI's
    ``_narrow_to_model_written``.
    """
    from dataclasses import replace

    from repowise.core.generation.models import MODEL_WRITTEN_PAGE_TYPES
    from repowise.core.generation.scope import build_cost_plans

    gen = {pid for pid in plan.generate_ids if pid.split(":", 1)[0] in MODEL_WRITTEN_PAGE_TYPES}
    stale = {pid for pid in plan.stale_ids if pid.split(":", 1)[0] in MODEL_WRITTEN_PAGE_TYPES}
    return replace(plan, generate_ids=gen, stale_ids=stale, cost_plans=build_cost_plans(gen))


def _resolve_generate_scope(config: dict, rehydrated: Any, gen_config: Any) -> Any:
    """Resolve a generate config + rehydrated repo into a :class:`ScopePlan`.

    The one place a ranked coverage seed is built (with ``repowise init``'s
    importance model), so the estimate and the job resolve the identical page
    set. The plan is then narrowed to model-written types, as in the CLI.
    """
    from repowise.core.generation.scope import build_ranked_seed, resolve_scope

    selection = config.get("selection") or {}
    ranked_seed: set[str] | None = None
    if selection.get("kind") == "ranked":
        pct = _ranked_coverage_pct(selection, len(rehydrated.parsed_files))
        ranked_seed = build_ranked_seed(
            parsed_files=rehydrated.parsed_files,
            graph_builder=rehydrated.graph_builder,
            config=gen_config,
            kg_ctx=rehydrated.kg_ctx,
            records=rehydrated.records,
            repo_name=rehydrated.repo_name,
            coverage_pct=pct,
        )
    plan = resolve_scope(
        records=rehydrated.records,
        intent=_build_generate_intent(config),
        cascade_mode=_effective_cascade(config),
        deps=rehydrated.deps,
        ranked_seed=ranked_seed,
    )
    return _narrow_plan_to_model_written(plan)


def _build_generation_config(repo_path: Path, config: dict, wiki_style: str) -> Any:
    """Build the ``GenerationConfig`` a generate job (or its estimate) runs with.

    Shared by the executor and the estimate endpoint so both resolve the same
    style / reasoning / language / concurrency, and therefore the same scope and
    cost. A per-request ``style`` override (carried in the job config) wins over
    the repo's default, matching the single-page regenerate contract.
    """
    from repowise.core.generation import GenerationConfig
    from repowise.core.generation.styles import resolve_style
    from repowise.core.reasoning import resolve_reasoning
    from repowise.core.repo_config import load_repo_config

    repo_cfg = load_repo_config(repo_path)
    effective_style = resolve_style(config.get("style") or wiki_style, repo_path=repo_path).name
    return GenerationConfig.from_repo_config(
        repo_cfg,
        reasoning=resolve_reasoning(config=repo_cfg),
        wiki_style=effective_style,
        language=repo_cfg.get("language", "en"),
        enable_onboarding=bool(repo_cfg.get("enable_onboarding", True)),
        max_concurrency=int(config.get("concurrency") or 12),
    )


async def _run_generate_job(
    *,
    job_id: str,
    repo_id: str,
    repo_path: Path,
    config: dict,
    session_factory: Any,
    provider: Any,
    vector_store: Any | None,
    fts: Any | None,
    progress: JobProgressCallback,
    exclude_patterns: list[str],
    wiki_style: str,
    start: float,
) -> None:
    """Run one scoped generation job through the shared core engine.

    Rehydrates the graph + git, resolves the requested scope + cascade, writes
    exactly that subset of pages, and records the terminal job status. The whole
    generate/persist/heal half is the same code the CLI ``repowise generate``
    runs, so behaviour matches across the two surfaces.
    """
    from repowise.core.pipeline.scoped_generation import (
        execute_scoped_generation,
        rehydrate_repo,
    )

    gen_config = _build_generation_config(repo_path, config, wiki_style)

    state = _load_state(repo_path)
    rehydrated = await rehydrate_repo(
        session_factory,
        repo_id,
        repo_path,
        generation_config=gen_config,
        exclude_patterns=exclude_patterns,
        include_submodules=bool(state.get("include_submodules", False)),
        include_nested_repos=bool(state.get("include_nested_repos", False)),
    )
    if rehydrated is None:
        raise RuntimeError("Repository has no wiki pages yet; run an index first.")

    # The shared helper, so the job writes exactly what its estimate priced.
    plan = _resolve_generate_scope(config, rehydrated, gen_config)

    generated_pages: list = []
    marked_stale = 0
    if plan.generate_ids:
        progress.on_phase_start("generation", len(plan.generate_ids))
        gen_result = await execute_scoped_generation(
            session_factory=session_factory,
            repo_id=repo_id,
            repo_path=repo_path,
            rehydrated=rehydrated,
            plan=plan,
            provider=provider,
            generation_config=gen_config,
            # The server vector store carries its own embedder.
            embedder=None,
            vector_store=vector_store,
            fts=fts,
            progress=progress,
            # Spend comes from the generated pages' token counts.
            cost_tracker=None,
            concurrency=gen_config.max_concurrency,
        )
        generated_pages = gen_result.generated_pages
        marked_stale = gen_result.marked_stale
    else:
        logger.info(
            "generate_job_empty_scope",
            job_id=job_id,
            unknown_page_ids=list(plan.unknown_page_ids),
        )

    await progress.drain_and_stop()

    elapsed = time.monotonic() - start
    total_input = sum(getattr(p, "input_tokens", 0) for p in generated_pages)
    total_output = sum(getattr(p, "output_tokens", 0) for p in generated_pages)
    from repowise.core.generation.models import count_stub_fallbacks

    pages_generated = len(generated_pages)
    stub_fallbacks = count_stub_fallbacks(generated_pages)

    async with get_session(session_factory) as session:
        await _complete_job_row(
            session,
            job_id,
            config,
            {
                "total_input_tokens": total_input,
                "total_output_tokens": total_output,
                "elapsed_seconds": round(elapsed, 1),
                "pages_generated": pages_generated,
                "pages_marked_stale": marked_stale,
                # On the job record so the UI can report an id that resolved to nothing.
                "unknown_page_ids": list(plan.unknown_page_ids),
            },
            completed_pages=pages_generated - stub_fallbacks,
            failed_pages=stub_fallbacks,
            total_pages=pages_generated,
        )
        total_pages, remaining_templates = await _repo_page_counts(session, repo_id)

    # Keep state.json in step, as the CLI does: docs_mode flips to "llm" only
    # once no template page remains.
    try:
        await asyncio.to_thread(
            _persist_generate_job_state,
            repo_path,
            total_pages=total_pages,
            remaining_templates=remaining_templates,
            pages_generated=pages_generated,
        )
    except Exception:
        logger.debug("state_json_update_failed", job_id=job_id, exc_info=True)

    logger.info(
        "generate_job_completed",
        job_id=job_id,
        pages=pages_generated,
        marked_stale=marked_stale,
        elapsed=round(elapsed, 1),
    )


async def _repo_page_counts(session: Any, repo_id: str) -> tuple[int, int]:
    """Return ``(total_pages, remaining_stub_pages)`` for a repo.

    Stubs count only model-written page types: structural pages stay
    ``template`` forever and would block the ``docs_mode -> llm`` flip.
    """
    from sqlalchemy import func as sa_func
    from sqlalchemy import select as sa_select

    from repowise.core.generation.models import MODEL_WRITTEN_PAGE_TYPES
    from repowise.core.persistence.models import Page

    total = int(
        (
            await session.execute(
                sa_select(sa_func.count()).select_from(Page).where(Page.repository_id == repo_id)
            )
        ).scalar_one()
    )
    stubs = int(
        (
            await session.execute(
                sa_select(sa_func.count())
                .select_from(Page)
                .where(
                    Page.repository_id == repo_id,
                    Page.page_type.in_(sorted(MODEL_WRITTEN_PAGE_TYPES)),
                    Page.provider_name == "template",
                )
            )
        ).scalar_one()
    )
    return total, stubs
