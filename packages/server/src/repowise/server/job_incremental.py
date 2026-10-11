"""Incremental page regeneration for sync jobs: rewrite only what changed."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog

from repowise.server.job_state import _load_state, _read_head_sha

logger = structlog.get_logger(__name__)


async def _incremental_page_regen(
    repo_path: Path,
    result: Any,
    llm_client: Any,
    job_config: dict,
    progress: Any | None,
    repo_wiki_style: str = "comprehensive",
    *,
    vector_store: Any | None = None,
    prior_pages: dict[str, Any] | None = None,
    session_factory: Any | None = None,
    repo_id: str | None = None,
) -> list:
    """Regenerate only wiki pages affected by recent changes.

    Uses the graph from the just-completed pipeline run + git diff to detect
    which pages need updating.  Returns a list of GeneratedPage objects (may
    be empty if nothing changed or no base ref is available).
    """
    try:
        stale_pages = await _stale_page_ages(session_factory, repo_id)

        plan = await asyncio.to_thread(
            _plan_incremental_page_regen,
            repo_path,
            result,
            job_config,
            stale_pages,
        )
        if plan is None:
            return []
        affected_parsed, affected_source, changed_count, affected_count, cascade_budget = plan

        logger.info(
            "incremental_page_regen_start",
            changed_files=changed_count,
            affected_pages=affected_count,
            cascade_budget=cascade_budget,
        )

        if progress:
            progress.on_phase_start("generation", affected_count)

        from repowise.core.generation import ContextAssembler, PageGenerator

        generation_config = _incremental_generation_config(repo_path, job_config, repo_wiki_style)
        assembler = ContextAssembler(generation_config, repo_path=repo_path)
        # As in the CLI: re-embed re-rendered pages, reuse unchanged ones.
        generator = PageGenerator(
            llm_client,
            assembler,
            generation_config,
            vector_store=vector_store,
            prior_pages=prior_pages or {},
            repo_path=repo_path,
        )

        pages = await generator.generate_all(
            affected_parsed,
            affected_source,
            result.graph_builder,
            result.repo_structure,
            result.repo_name,
            git_meta_map=result.git_meta_map,
            repo_path=repo_path,
        )

        logger.info("incremental_page_regen_done", pages=len(pages))
        return pages

    except Exception as exc:
        logger.warning("incremental_page_regen_failed", error=str(exc))
        return []


async def _stale_page_ages(session_factory: Any | None, repo_id: str | None) -> dict[str, float]:
    """Stale-page ages, so a constrained cascade budget reaches the oldest stale
    pages first (issues #847 / #851)."""
    if session_factory is None or repo_id is None:
        return {}
    try:
        from repowise.core.persistence import get_session, get_stale_file_page_ages

        async with get_session(session_factory) as session:
            return await get_stale_file_page_ages(session, repo_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("incremental_page_regen_stale_lookup_failed", error=str(exc))
        return {}


def _incremental_generation_config(repo_path: Path, job_config: dict, repo_wiki_style: str) -> Any:
    """The ``GenerationConfig`` a sync's incremental regeneration runs with."""
    from repowise.core.generation import GenerationConfig

    # A per-page style override in the job config wins over the repo default.
    from repowise.core.generation.styles import resolve_style
    from repowise.core.reasoning import resolve_reasoning
    from repowise.core.repo_config import load_repo_config

    effective_style = resolve_style(
        job_config.get("style") or repo_wiki_style, repo_path=repo_path
    ).name
    repo_cfg = load_repo_config(repo_path)
    return GenerationConfig.from_repo_config(
        repo_cfg,
        reasoning=resolve_reasoning(config=repo_cfg),
        wiki_style=effective_style,
        language=repo_cfg.get("language", "en"),
        # parsed_files holds only the changed files, so repo-wide pages
        # (level 3 and up) would be written from a truncated view.
        file_pages_only=True,
    )


def _plan_incremental_page_regen(
    repo_path: Path,
    result: Any,
    job_config: dict,
    stale_pages: dict[str, float],
) -> tuple[list[Any], dict[str, Any], int, int, int] | None:
    """Build an incremental regeneration plan without blocking the event loop.

    The caller runs this whole synchronous unit (git, ``ChangeDetector``, graph
    ranking) in a worker thread.
    """
    base_ref = _load_state(repo_path).get("last_sync_commit") or job_config.get("before")
    if not base_ref:
        logger.info("incremental_page_regen_skipped", reason="no_base_ref")
        return None

    head = _read_head_sha(repo_path)
    if not head:
        return None
    if head == base_ref:
        logger.info("incremental_page_regen_skipped", reason="no_new_commits")
        return None

    from repowise.core.ingestion import ChangeDetector
    from repowise.core.ingestion.change_detector import compute_adaptive_budget

    detector = ChangeDetector(repo_path)
    file_diffs = detector.get_changed_files(base_ref, head)
    if not file_diffs:
        return None

    cascade_budget = compute_adaptive_budget(file_diffs, result.file_count)
    affected = detector.get_affected_pages(
        file_diffs,
        result.graph_builder.graph(),
        cascade_budget,
        pagerank=result.graph_builder.pagerank(),
        stale_pages=stale_pages,
    )
    if not affected.regenerate:
        logger.info("incremental_page_regen_skipped", reason="no_affected_pages")
        return None

    regen_set = set(affected.regenerate)
    affected_parsed = [pf for pf in result.parsed_files if pf.file_info.path in regen_set]
    affected_source = {p: s for p, s in result.source_map.items() if p in regen_set}
    return (
        affected_parsed,
        affected_source,
        len(file_diffs),
        len(affected.regenerate),
        cascade_budget,
    )
