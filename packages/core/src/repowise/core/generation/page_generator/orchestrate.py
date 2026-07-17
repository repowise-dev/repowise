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
        on_page_ready: Callable[[GeneratedPage], None] | None = None,
        kg_modules: list[dict] | None = None,
        kg_data: dict | None = None,
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
        # Fired with the full GeneratedPage the instant it completes (in
        # addition to on_page_done, which only gets the page_type). Lets a
        # caller persist/stream pages incrementally — e.g. the hosted indexer
        # flushing pages.json per page so a budget cutoff yields partial docs
        # instead of nothing. Optional + best-effort; never blocks generation.
        self.on_page_ready = on_page_ready
        self.on_total_known = on_total_known
        self.on_subphase = on_subphase
        self.git_meta_map = git_meta_map
        self.resume = resume
        self.repo_path = repo_path
        self.external_systems = external_systems or []
        # Curated wiki modules from the IN-MEMORY pipeline result. The kg_ctx
        # file fallback below is one run stale on update and absent on a
        # fresh init (the artifact is written AFTER generation) — the live
        # repowise run shipped community-grouped module pages because of it.
        self.kg_modules = kg_modules or []

        # ---- Graph metrics ----
        self.graph = graph_builder.graph()
        self.pagerank = graph_builder.pagerank()
        self.betweenness = graph_builder.betweenness_centrality()
        self.community = graph_builder.community_detection()
        self.sccs = graph_builder.strongly_connected_components()

        # ---- Per-file signal maps ----
        self.dead_code_by_file = build_dead_code_map(dead_code_report)
        self.decisions_by_file, self.decisions_all = build_decision_maps(decision_report)

        # ---- KG context (per-file knowledge graph lookups) ----
        from repowise.core.generation.kg_context import KnowledgeGraphContext

        # Prefer the in-memory KG (the pipeline result's export dict): the
        # artifact file is only written during persistence — AFTER this
        # generation pass — so on a fresh init the file path below finds
        # nothing and every kg_ctx-derived page (layer pages, tour context,
        # file layers) silently vanished from first-run wikis.
        rp = None
        if repo_path:
            rp = Path(repo_path) if not isinstance(repo_path, Path) else repo_path
        if kg_data is not None:
            self.kg_ctx = KnowledgeGraphContext(None, rp, data=kg_data)
        else:
            kg_path = None
            if rp:
                for candidate in [
                    rp / ".repowise" / "knowledge-graph.json",
                    rp / ".understand-anything" / "knowledge-graph.json",
                ]:
                    if candidate.exists():
                        kg_path = candidate
                        break
            self.kg_ctx = KnowledgeGraphContext(kg_path)

        # ---- Run bookkeeping ----
        self.semaphore = asyncio.Semaphore(self.config.max_concurrency)
        self.completed_page_summaries: dict[str, str] = {}
        self.completed_ids: set[str] = set()
        self.job_id: str | None = None
        self.file_page_contexts: dict[str, FilePageContext] = {}

        # Guided-tour ordering + the layer spine, both derived after selection
        # and reused by level-8 onboarding, the repo overview, and the agent
        # surface. Empty until _compute_ia() runs.
        self.tour_stops: list[dict] = []
        self.layer_order: list[str] = []

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
        if self.job_system is not None and self.resume and self.vector_store is not None:
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

        kg_scores = _compute_kg_file_scores(self.kg_ctx)

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
                kg_file_scores=kg_scores or None,
                # Curated wiki modules: prefer the in-memory pipeline
                # result (fresh); the artifact file is absent on first init
                # and one run stale on update. Inert unless
                # module_grouping == "curated".
                kg_modules=self.kg_modules or self.kg_ctx.get_modules() or None,
                # Per-file question demand from session transcripts: tilts the
                # file_page budget toward modules agents ask about. Empty (no
                # session history / disabled) leaves selection uniform.
                demand=self._compute_demand() or None,
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
            kg_file_scores=kg_scores or None,
        )

        # Deterministic coverage tail (Phase G): code files the budget dropped,
        # rendered by the same zero-LLM template path as tier-2. Disjoint from
        # sel_file_paths by construction (select_pages excludes the selected set).
        self.tail_paths = set(getattr(selection, "deterministic_tail_paths", []) or [])

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

    def _compute_demand(self) -> dict[str, int]:
        """Per-file question demand for the FAQ-weighted budget tilt.

        Best-effort transcript sweep; any failure (no repo path, no session
        history, mining error) yields an empty map and selection stays
        uniform. Runs only on real generation — the cost estimator builds its
        own demand-free SelectionInputs, so estimates are untouched.
        """
        if not self.repo_path:
            return {}
        try:
            from repowise.core.repo_config import load_repo_config
            from repowise.core.sessions.miners.demand import (
                aggregate_file_demand,
                demand_summary_line,
            )

            repo_config = load_repo_config(self.repo_path)
            demand = aggregate_file_demand(self.repo_path, repo_config=repo_config)
            # Surface the usage-weighting to the CLI (shown after generation).
            self.gen.faq_demand_summary = demand_summary_line(demand)
            return demand
        except Exception as exc:  # never let mining break generation
            log.warning("page_selection.demand_failed", error=str(exc))
            return {}

    def _announce_total(self) -> None:
        counts = self.selection.counts()
        layer_page_count = 0
        if self.kg_ctx.available:
            layer_page_count = sum(
                1
                for l in self.kg_ctx.get_layers()
                if len([n for n in l.get("nodeIds", []) if n.startswith("file:")]) >= 3
            )
        estimated_total = (
            counts["api_contract"]
            + counts["symbol_spotlight"]
            + counts["file_page"]
            + counts["scc_page"]
            + counts["module_page"]
            + layer_page_count
            + int(self.selection.emit_repo_overview)
            + int(self.selection.emit_arch_diagram)
            + counts["infra_page"]
            # Deterministic coverage tail also produces (zero-LLM) pages.
            + len(getattr(self.selection, "deterministic_tail_paths", []))
        )
        remaining_total = max(0, estimated_total - len(self.completed_ids))
        if self.on_total_known is not None:
            self.on_total_known(remaining_total)
        if self.job_system is not None and self.job_id is not None:
            self.job_system.start_job(self.job_id, estimated_total)

    def _file_import_edges(self) -> list[tuple[str, str]]:
        """``(src, dst)`` import edges between file nodes (src imports dst)."""
        edges: list[tuple[str, str]] = []
        try:
            for src, dst in self.graph.edges():
                if isinstance(src, str) and isinstance(dst, str):
                    edges.append((src, dst))
        except Exception:
            pass
        return edges

    def _compute_ia(self) -> None:
        """Derive the guided-tour ordering and the layer spine after selection.

        Both reuse already-computed signals (selection allow-sets, PageRank,
        the import graph) and reference only pages that will exist, so neither
        spawns new LLM work.
        """
        from ..layers import compute_layer_order, infer_layer
        from ..tour import build_tour

        import_edges = self._file_import_edges()

        # When the indexed KG carries the curated tour (project.graph_mode is
        # written only by the curation pass), adopt it wholesale instead of
        # re-deriving a second, divergent tour from the raw graph: the curated
        # tour knows the repo's honesty mode (flow/sparse/structural), walks
        # imports-type edges only, and excludes support paths — and the wiki's
        # file cards already cite its steps. One tour, every surface.
        if self.kg_ctx.available and self.kg_ctx.get_graph_mode():
            self.tour_stops = [dict(s) for s in self.kg_ctx.get_tour()]
        if not self.tour_stops:
            # Tour: ordered stops over the selected file/infra pages + overview.
            stops = build_tour(
                self.parsed_files,
                self.pagerank,
                import_edges,
                file_page_paths=self.sel_file_paths,
                infra_paths=self.sel_infra_paths,
                repo_name=self.repo_name,
            )
            self.tour_stops = [s.as_dict() for s in stops]

        # Layer spine: every documented file gets a layer (KG when present,
        # path-based inference otherwise), then layers are ordered top→bottom
        # by inter-layer dependency direction.
        lang_by_path = {
            p.file_info.path: (getattr(p.file_info, "language", "") or "").lower()
            for p in self.parsed_files
            if getattr(p, "file_info", None)
        }
        file_layers: dict[str, str] = {}
        for path in self.sel_file_paths:
            kg_fc = self.kg_ctx.get_file_context(path) if self.kg_ctx.available else None
            file_layers[path] = (
                kg_fc.layer_name if kg_fc and kg_fc.layer_name else ""
            ) or infer_layer(path, lang_by_path.get(path))
        self.layer_order = compute_layer_order(file_layers, import_edges)

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
                    # Hand the full page to a streaming sink (incremental
                    # persistence). Best-effort: a sink error must not drop the
                    # page or abort the level.
                    if self.on_page_ready is not None:
                        try:
                            self.on_page_ready(result)
                        except Exception as exc:  # noqa: BLE001
                            log.debug("on_page_ready.failed", error=str(exc))
                    # A page reused verbatim from the prior run already has an
                    # identical vector in any store that survives across runs;
                    # re-embedding it re-bills the embedder for every unchanged
                    # page on every update. Ephemeral stores start empty each
                    # run and still need it.
                    if self.vector_store is not None and not (
                        result.metadata.get("reused_from_prior_run")
                        and getattr(self.vector_store, "persists_across_runs", False)
                    ):
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
            except BaseException:
                # Cancellation (Ctrl+C teardown): CancelledError is a
                # BaseException, so it skips the handler above. If the cancel
                # landed while this page was still queued on the semaphore,
                # ``coro`` was never started — close it so interpreter
                # shutdown doesn't spray one "coroutine ... was never
                # awaited" RuntimeWarning per pending page (issue #358).
                # close() is a no-op on a coroutine that already ran.
                coro.close()
                raise

        tasks = [guarded_named(pid, c) for pid, c in named_coros]
        results = await asyncio.gather(*tasks)
        # Embed the whole level in one batch before declaring it done — the
        # next level's RAG search depends on these landing in the store.
        # Embedding is a RAG enhancement, not load-bearing, so a failure must
        # not abort generation — but it MUST be visible: a debug-level
        # swallow here hid a 300k-token request rejection that silently lost
        # every file-page embedding on init (`repowise reindex` repairs).
        if embed_items and self.vector_store is not None:
            try:
                await self.vector_store.embed_batch(embed_items)
            except Exception as e:
                log.warning(
                    "rag.embed_batch_failed",
                    level=level,
                    count=len(embed_items),
                    error=str(e),
                    hint="semantic search will miss these pages; run `repowise reindex` to repair",
                )
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
        self._compute_ia()

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

        # Level 5 (layer_page) — one page per KG layer.
        all_pages.extend(await self.run_level(_levels.build_level5_coros(self), 5))

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

        # Attach the IA spine to the repo overview so the web reader and the
        # MCP get_overview both expose the topology tour order + ordered layers.
        if self.tour_stops or self.layer_order:
            for page in final_pages:
                if page.page_type == "repo_overview":
                    if self.tour_stops:
                        page.metadata["guided_tour"] = self.tour_stops
                    if self.layer_order:
                        page.metadata["layer_order"] = self.layer_order
                    break

        # Post-generation: repair mermaid diagrams so illegal node IDs / unquoted
        # labels in LLM output don't break the whole diagram in the renderer.
        try:
            from ..mermaid_safety import sanitize_pages

            fixed = sanitize_pages(all_pages)
            if fixed:
                log.info("mermaid_safety.applied", pages_changed=fixed)
        except Exception as exc:
            log.debug("mermaid_safety.failed", error=str(exc))

        # Post-generation: resolve backtick refs into wiki links + backlinks.
        try:
            from ..interlinking import attach_wiki_links_and_backlinks

            attach_wiki_links_and_backlinks(
                all_pages,
                self.parsed_files,
                # On incremental updates only the affected pages are in
                # all_pages; the persisted ids keep resolution repo-wide.
                prior_page_ids=list(self.gen._prior_pages or {}),
            )
        except Exception as exc:
            log.debug("interlinking.failed", error=str(exc))

        # Post-generation: graph-derived related pages. Runs AFTER
        # interlinking so prose-derived wiki_links win dedup and related
        # entries only fill the gaps.
        try:
            from ..related_pages import attach_related_pages

            attach_related_pages(
                all_pages,
                import_edges=self._file_import_edges(),
                git_meta_map=self.git_meta_map,
                module_groups=self.sel_module_groups,
                pagerank=self.pagerank,
                # On incremental updates only the affected pages are in
                # all_pages; the persisted ids keep resolution repo-wide.
                prior_page_ids=list(self.gen._prior_pages or {}),
            )
        except Exception as exc:
            log.debug("related_pages.failed", error=str(exc))

        # Post-generation: link KG tour steps to wiki page IDs.
        if self.kg_ctx.available and self.repo_path:
            try:
                from ..kg_enrichment import enrich_tour_with_wiki_links

                rp = (
                    Path(self.repo_path) if not isinstance(self.repo_path, Path) else self.repo_path
                )
                kg_path = rp / ".repowise" / "knowledge-graph.json"
                if kg_path.exists():
                    enrich_tour_with_wiki_links(kg_path, all_pages)
            except Exception as exc:
                log.debug("kg_enrichment.failed", error=str(exc))

        if self.job_system is not None and self.job_id is not None:
            self.job_system.complete_job(self.job_id)

        log.info(
            "Generation complete",
            total_pages=len(all_pages),
            provider=self.gen._provider.provider_name,
            model=self.gen._provider.model_name,
        )
        return all_pages


def _compute_kg_file_scores(kg_ctx: Any) -> dict[str, float]:
    """Derive per-file KG bonus scores from tour membership and role."""
    if not kg_ctx.available:
        return {}
    scores: dict[str, float] = {}
    for layer in kg_ctx.get_layers():
        for node_id in layer.get("nodeIds", []):
            if node_id.startswith("file:"):
                fp = node_id[5:]
                fc = kg_ctx.get_file_context(fp)
                if fc:
                    bonus = 0.0
                    if fc.tour_step:
                        bonus += 0.30
                    if fc.role == "edge_connector":
                        bonus += 0.15
                    if bonus > scores.get(fp, 0.0):
                        scores[fp] = bonus
    return scores


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
