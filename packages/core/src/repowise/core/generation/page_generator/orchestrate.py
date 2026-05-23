"""Level-by-level orchestration for ``PageGenerator.generate_all``.

``run_generate_all`` builds a :class:`_GenerationRun` that holds the shared
per-run state (graph metrics, selection allow-sets, job bookkeeping, the
concurrency semaphore) and drives the ordered generation levels. The
per-level coroutine builders live in ``levels.py`` and read this state object.

Behaviour is identical to the previous single-method implementation; this is
purely a structural split to satisfy the project's 400-line ceiling.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from ..context_assembler import FilePageContext
from ..models import GeneratedPage
from . import levels as _levels
from .helpers import (
    _CODE_LANGUAGES,
    _is_infra_file,
    _select_clone_representatives,
    build_dead_code_map,
    build_decision_maps,
    overview_summary,
)
from .tiering import partition_file_tiers

if TYPE_CHECKING:
    from .core import PageGenerator

log = structlog.get_logger(__name__)


class _GenerationRun:
    """Mutable per-call state for one ``generate_all`` invocation."""

    def __init__(
        self,
        gen: PageGenerator,
        *,
        parsed_files: list[Any],
        source_map: dict[str, bytes],
        graph_builder: Any,
        repo_structure: Any,
        repo_name: str,
        job_system: Any | None,
        on_page_done: Callable[[str], None] | None,
        on_total_known: Callable[[int], None] | None,
        on_subphase: Callable[[str, int | None], None] | None,
        git_meta_map: dict[str, dict] | None,
        resume: bool,
        repo_path: Path | str | None,
        dead_code_report: Any | None,
        decision_report: Any | None,
        external_systems: list[dict] | None,
    ) -> None:
        self.gen = gen
        self.config = gen._config
        self.vector_store = gen._vector_store
        self.parsed_files = parsed_files
        self.source_map = source_map
        self.graph_builder = graph_builder
        self.repo_structure = repo_structure
        self.repo_name = repo_name
        self.job_system = job_system
        self.on_page_done = on_page_done
        self.on_total_known = on_total_known
        self.on_subphase = on_subphase
        self.git_meta_map = git_meta_map
        self.resume = resume
        self.repo_path = repo_path
        self.external_systems = external_systems or []

        # ---- Graph metrics ----
        self.graph = graph_builder.graph()
        self.pagerank = graph_builder.pagerank()
        self.betweenness = graph_builder.betweenness_centrality()
        self.community = graph_builder.community_detection()
        self.sccs = graph_builder.strongly_connected_components()

        # ---- Per-file signal maps ----
        self.dead_code_by_file = build_dead_code_map(dead_code_report)
        self.decisions_by_file, self.decisions_all = build_decision_maps(decision_report)

        # ---- Run bookkeeping ----
        self.semaphore = asyncio.Semaphore(self.config.max_concurrency)
        self.completed_page_summaries: dict[str, str] = {}
        self.completed_ids: set[str] = set()
        self.job_id: str | None = None
        self.file_page_contexts: dict[str, FilePageContext] = {}

        # Selection allow-sets (populated by _compute_selection).
        self.selection: Any = None
        self.code_files: list[Any] = []
        self.sel_file_paths: set[str] = set()
        self.sel_api_paths: set[str] = set()
        self.sel_infra_paths: set[str] = set()
        self.sel_module_groups: list[Any] = []
        self.sel_scc_groups: list[Any] = []
        self.tier1_paths: set[str] = set()
        self.tier2_paths: set[str] = set()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup_job(self) -> None:
        """Create the resume job and, on resume, seed completed page ids."""
        if self.job_system is None:
            return
        repo_path_str = (
            str(Path(self.repo_path).resolve())
            if self.repo_path
            else str(getattr(self.repo_structure, "root_path", "."))
        )
        # On resume, query the vector store directly — it is the ground truth.
        if self.resume and self.vector_store is not None:
            # Note: caller drives this synchronously enough; resume seeding is
            # awaited in execute() to keep __init__ side-effect free.
            pass
        self.job_id = self.job_system.create_job(
            repo_path_str,
            self.config,
            self.gen._provider.provider_name,
            self.gen._provider.model_name,
        )

    async def _seed_resume(self) -> None:
        if (
            self.job_system is not None
            and self.resume
            and self.vector_store is not None
        ):
            self.completed_ids = await self.vector_store.list_page_ids()
            if self.completed_ids:
                log.info(
                    "Resuming generation from vector store",
                    already_completed=len(self.completed_ids),
                )

    def _compute_selection(self) -> None:
        """Run the selection subsystem and derive the level allow-sets."""
        code_files = [
            p
            for p in self.parsed_files
            if not p.file_info.is_api_contract
            and not _is_infra_file(p)
            and p.file_info.language in _CODE_LANGUAGES
        ]

        # Near-clone dedupe runs before scoring so clone losers never consume
        # scoring budget. Entry points are never dropped.
        parsed_files_for_selection = self.parsed_files
        if getattr(self.config, "dedupe_near_clones", True):
            drop_paths = _select_clone_representatives(code_files, self.pagerank)
            if drop_paths:
                log.info("page_selection.clone_dedupe", dropped=len(drop_paths))
                code_files = [p for p in code_files if p.file_info.path not in drop_paths]
                parsed_files_for_selection = [
                    p for p in self.parsed_files if p.file_info.path not in drop_paths
                ]

        try:
            community_info_map = self.graph_builder.community_info() or {}
        except Exception:
            community_info_map = {}

        from ..selection import SelectionInputs, select_pages

        selection = select_pages(
            SelectionInputs(
                parsed_files=parsed_files_for_selection,
                pagerank=self.pagerank,
                betweenness=self.betweenness,
                community=self.community,
                community_info=community_info_map,
                sccs=list(self.sccs),
                git_meta_map=self.git_meta_map,
                config=self.config,
            )
        )

        self.selection = selection
        self.sel_file_paths = set(selection.file_page_paths)
        self.sel_api_paths = set(selection.api_contract_paths)
        self.sel_infra_paths = set(selection.infra_paths)
        self.sel_module_groups = list(selection.module_groups)
        self.sel_scc_groups = list(selection.scc_groups)

        # Tiered doc generation: split the selected file pages into a full-LLM
        # tier-1 and a deterministic template-only tier-2. When tier1_top_n is
        # None this puts every selected page in tier-1 (no behaviour change).
        self.tier1_paths, self.tier2_paths = partition_file_tiers(
            self.sel_file_paths,
            self.pagerank,
            getattr(self.config, "tier1_top_n", None),
        )

        # Sort code_files for stable level-2 ordering: selected files first
        # (so dep summaries land in the store earliest), then by PageRank desc.
        self.code_files = sorted(
            code_files,
            key=lambda p: (
                p.file_info.path not in self.sel_file_paths,
                not p.file_info.is_entry_point,
                -self.pagerank.get(p.file_info.path, 0.0),
            ),
        )

    def _announce_total(self) -> None:
        counts = self.selection.counts()
        estimated_total = (
            counts["api_contract"]
            + counts["symbol_spotlight"]
            + counts["file_page"]
            + counts["scc_page"]
            + counts["module_page"]
            + int(self.selection.emit_repo_overview)
            + int(self.selection.emit_arch_diagram)
            + counts["infra_page"]
        )
        remaining_total = max(0, estimated_total - len(self.completed_ids))
        if self.on_total_known is not None:
            self.on_total_known(remaining_total)
        if self.job_system is not None and self.job_id is not None:
            self.job_system.start_job(self.job_id, estimated_total)

    # ------------------------------------------------------------------
    # Level runner
    # ------------------------------------------------------------------

    async def run_level(
        self, named_coros: list[tuple[str, Any]], level: int
    ) -> list[GeneratedPage]:
        """Run one level's coroutines under the shared semaphore + embed batch."""
        if self.job_system is not None and self.job_id is not None:
            self.job_system.update_level(self.job_id, level)

        # Pages finished during this level, collected for a single batched
        # embed at the end. Embedding the whole wave in one call amortises the
        # embedder round-trip and the level drains before the next level's RAG
        # search runs, so there is no freshness regression.
        embed_items: list[tuple[str, str, dict]] = []

        async def guarded_named(page_id: str, coro: Any) -> Any:
            try:
                async with self.semaphore:
                    result = await coro

                if isinstance(result, GeneratedPage):
                    # Summary capture is cheap (string ops) — keep inline so
                    # the next page's context assembly sees it immediately.
                    self.completed_page_summaries[result.target_path] = overview_summary(
                        result.content
                    )
                    # Progress tick fires the moment the page is ready.
                    if self.on_page_done is not None:
                        self.on_page_done(result.page_type)
                    if self.vector_store is not None:
                        embed_items.append(_embed_item(result))
                return result
            except Exception as exc:
                if self.job_system is not None and self.job_id is not None:
                    self.job_system.fail_page(self.job_id, page_id, str(exc))
                log.error(
                    "page_generation_failed",
                    page_id=page_id,
                    level=level,
                    error=str(exc),
                )
                return exc  # return as value so gather works

        tasks = [guarded_named(pid, c) for pid, c in named_coros]
        results = await asyncio.gather(*tasks)
        # Embed the whole level in one batch before declaring it done — the
        # next level's RAG search depends on these landing in the store.
        # Embedding is a RAG enhancement, not load-bearing, so failures are
        # swallowed at debug level.
        if embed_items and self.vector_store is not None:
            try:
                await self.vector_store.embed_batch(embed_items)
            except Exception as e:
                log.debug("rag.embed_batch_failed", count=len(embed_items), error=str(e))
        pages = [r for r in results if isinstance(r, GeneratedPage)]
        if self.job_system is not None and self.job_id is not None:
            for r in pages:
                self.job_system.complete_page(self.job_id, r.page_id)
        return pages

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    async def execute(self) -> list[GeneratedPage]:
        self._setup_job()
        await self._seed_resume()
        self._compute_selection()
        self._announce_total()

        all_pages: list[GeneratedPage] = []

        # Levels 0 (api_contract) + 1 (symbol_spotlight) share no data
        # dependencies, so they run in one merged batch.
        level01 = _levels.build_level01_coros(self)
        all_pages.extend(await self.run_level(level01, 1))

        # Level 2 (file_page) needs context assembly + topo ordering.
        level2 = await _levels.build_level2_coros(self)
        all_pages.extend(await self.run_level(level2, 2))

        # Level 3 (scc_page).
        all_pages.extend(await self.run_level(_levels.build_level3_coros(self), 3))

        # Level 4 (module_page).
        all_pages.extend(await self.run_level(_levels.build_level4_coros(self), 4))

        # Levels 6 (repo_overview + architecture_diagram), 7 (infra_page),
        # and 8 (onboarding) share no data dependencies — run merged.
        final = (
            _levels.build_level6_coros(self)
            + _levels.build_level7_coros(self)
            + _levels.build_level8_coros(self)
        )
        final_pages = await self.run_level(final, 8)
        # Tag promoted onboarding slots (repo_overview / architecture_diagram).
        self.gen._tag_promoted_pages(final_pages)
        all_pages.extend(final_pages)

        # Post-generation: resolve backtick refs into wiki links + backlinks.
        try:
            from ..interlinking import attach_wiki_links_and_backlinks

            attach_wiki_links_and_backlinks(all_pages, self.parsed_files)
        except Exception as exc:
            log.debug("interlinking.failed", error=str(exc))

        if self.job_system is not None and self.job_id is not None:
            self.job_system.complete_job(self.job_id)

        log.info(
            "Generation complete",
            total_pages=len(all_pages),
            provider=self.gen._provider.provider_name,
            model=self.gen._provider.model_name,
        )
        return all_pages


def _embed_item(page: GeneratedPage) -> tuple[str, str, dict]:
    """Build the ``(page_id, text, metadata)`` tuple for embedding."""
    summary = overview_summary(page.content)
    return (
        page.page_id,
        page.content,
        {
            "page_type": page.page_type,
            "target_path": page.target_path,
            "content": page.content[:600],
            "summary": summary,
        },
    )


async def run_generate_all(gen: PageGenerator, **kwargs: Any) -> list[GeneratedPage]:
    """Entry point used by ``PageGenerator.generate_all``."""
    run = _GenerationRun(gen, **kwargs)
    return await run.execute()
