"""Page generator — converts context dataclasses into GeneratedPage objects.

PageGenerator is the main orchestration layer.  It:
    1. Calls ContextAssembler to build template context from ingestion data.
    2. Renders the Jinja2 user-prompt template.
    3. Calls the provider with the rendered prompt + system prompt constant.
    4. Wraps the response in a GeneratedPage.
    5. Manages concurrency (asyncio.Semaphore) and prompt caching (SHA256).

System prompts are module-level constants — the same string per page type on
every call.  This enables Anthropic server-side prefix caching.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jinja2
import structlog

from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY
from repowise.core.ingestion.models import ParsedFile, RepoStructure
from repowise.core.providers.llm.base import BaseProvider, GeneratedResponse

from .context_assembler import ContextAssembler, FilePageContext
from .models import (
    GENERATION_LEVELS,
    GeneratedPage,
    GenerationConfig,
    compute_page_id,
    compute_source_hash,
)
# Language name mapping for prompt clarity
_LANGUAGE_NAMES = {
    "en": "English",
    "ru": "Russian",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "pl": "Polish",
    "tr": "Turkish",
    "ar": "Arabic",
    "hi": "Hindi",
}

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# System prompts — one per page type (constant strings for prefix caching)
# ---------------------------------------------------------------------------

SYSTEM_PROMPTS: dict[str, str] = {
    "file_page": (
        "You are repowise, an expert technical documentation generator. "
        "Your task is to produce comprehensive, accurate wiki pages from source code. "
        "Output markdown only. Do not include preamble or apologies. "
        "Required sections: ## Overview, ## Public API, ## Dependencies, ## Usage Notes."
    ),
    "symbol_spotlight": (
        "You are repowise, an expert technical documentation generator. "
        "Write a detailed spotlight page for a single code symbol. "
        "Output markdown only. "
        "Required sections: ## Purpose, ## Signature, ## Parameters, ## Returns, ## Example Usage."
    ),
    "module_page": (
        "You are repowise, an expert technical documentation generator. "
        "Write a module-level overview page summarising all files in the module. "
        "Output markdown only. "
        "Required sections: ## Overview, ## Public API Summary, ## Architecture Notes."
    ),
    "scc_page": (
        "You are repowise, an expert technical documentation generator. "
        "Document this circular dependency cycle and provide actionable refactoring advice. "
        "Output markdown only. "
        "Required sections: ## Cycle Description, ## Files Involved, ## Why This Exists, "
        "## Refactoring Suggestions."
    ),
    "repo_overview": (
        "You are repowise, an expert technical documentation generator. "
        "Write a high-level repository overview suitable for onboarding new developers. "
        "Output markdown only. "
        "Required sections: ## Project Summary, ## Technology Stack, ## Entry Points, ## Architecture."
    ),
    "architecture_diagram": (
        "You are repowise, an expert technical documentation generator. "
        "Generate an architecture overview with a Mermaid diagram. "
        "You MUST include a fenced mermaid block with graph TD showing key dependencies. "
        "Output markdown only."
    ),
    "api_contract": (
        "You are repowise, an expert technical documentation generator. "
        "Document this API contract file for developers integrating with the service. "
        "Output markdown only. "
        "Required sections: ## Overview, ## Endpoints, ## Schemas, ## Authentication, ## Examples."
    ),
    "infra_page": (
        "You are repowise, an expert technical documentation generator. "
        "Document this infrastructure file for DevOps and platform engineers. "
        "Output markdown only. "
        "Required sections: ## Purpose, ## Key Targets/Stages, ## Configuration, ## Operational Notes."
    ),
    "diff_summary": (
        "You are repowise, an expert technical documentation generator. "
        "Summarise the changes between two git refs and their documentation impact. "
        "Output markdown only. "
        "Required sections: ## Summary, ## Changed Files, ## Symbol Changes, ## Affected Documentation."
    ),
    "cross_package": (
        "You are repowise, an expert technical documentation generator. "
        "Document the cross-package boundary and integration points. "
        "Output markdown only. "
        "Required sections: ## Overview, ## Package Boundaries, ## Integration Points."
    ),
}

_INFRA_LANGUAGES = _LANG_REGISTRY.infra_languages()
_INFRA_FILENAMES = frozenset({"Dockerfile", "Makefile", "GNUmakefile"})
_CODE_LANGUAGES = _LANG_REGISTRY.code_languages()


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(UTC).isoformat()


class PageGenerator:
    """Generate wiki pages by rendering prompts and calling an LLM provider.

    Args:
        provider:   Any BaseProvider implementation.
        assembler:  ContextAssembler instance.
        config:     GenerationConfig controlling budget, concurrency, caching.
        jinja_env:  Optional Jinja2 Environment (defaults to FileSystemLoader
                    pointing at the templates/ directory next to this file).
    """

    def __init__(
        self,
        provider: BaseProvider,
        assembler: ContextAssembler,
        config: GenerationConfig,
        jinja_env: jinja2.Environment | None = None,
        vector_store: Any | None = None,
        language: str = "en",
    ) -> None:
        self._provider = provider
        self._assembler = assembler
        self._config = config
        self._vector_store = vector_store
        self._language = language
        self._cache: dict[str, GeneratedResponse] = {}

        if jinja_env is None:
            templates_dir = Path(__file__).parent / "templates"
            loader = jinja2.FileSystemLoader(str(templates_dir))
            jinja_env = jinja2.Environment(
                loader=loader,
                undefined=jinja2.StrictUndefined,
                autoescape=False,
            )
        self._jinja_env = jinja_env
    # ------------------------------------------------------------------
    # Per-type generation methods
    # ------------------------------------------------------------------

    async def generate_file_page(
        self,
        parsed: ParsedFile,
        graph: Any,
        pagerank: dict[str, float],
        betweenness: dict[str, float],
        community: dict[str, int],
        source_bytes: bytes,
    ) -> GeneratedPage:
        ctx = self._assembler.assemble_file_page(
            parsed, graph, pagerank, betweenness, community, source_bytes
        )
        user_prompt = self._render("file_page.j2", ctx=ctx)
        response = await self._call_provider("file_page", user_prompt, str(uuid.uuid4()))
        return self._build_generated_page(
            "file_page",
            parsed.file_info.path,
            f"File: {parsed.file_info.path}",
            response,
            compute_source_hash(user_prompt),
            GENERATION_LEVELS["file_page"],
        )

    async def generate_symbol_spotlight(
        self,
        symbol: Any,
        parsed: ParsedFile,
        pagerank: dict[str, float],
        graph: Any,
        source_map: dict[str, bytes] | None = None,
    ) -> GeneratedPage:
        ctx = self._assembler.assemble_symbol_spotlight(
            symbol,
            parsed,
            pagerank,
            graph,
            source_bytes=(source_map or {}).get(parsed.file_info.path, b""),
        )
        user_prompt = self._render("symbol_spotlight.j2", ctx=ctx)
        response = await self._call_provider("symbol_spotlight", user_prompt, str(uuid.uuid4()))
        return self._build_generated_page(
            "symbol_spotlight",
            f"{parsed.file_info.path}::{symbol.name}",
            f"Symbol: {symbol.qualified_name}",
            response,
            compute_source_hash(user_prompt),
            GENERATION_LEVELS["symbol_spotlight"],
        )

    async def generate_module_page(
        self,
        module_path: str,
        language: str,
        file_contexts: list[FilePageContext],
        graph: Any,
        git_meta_map: dict[str, dict] | None = None,
        page_summaries: dict[str, str] | None = None,
    ) -> GeneratedPage:
        ctx = self._assembler.assemble_module_page(
            module_path,
            language,
            file_contexts,
            graph,
            page_summaries=page_summaries,
        )
        module_git_summary = None
        if git_meta_map:
            from collections import Counter

            file_paths = [fc.file_path for fc in file_contexts]
            metas = [git_meta_map[f] for f in file_paths if f in git_meta_map]
            if metas:
                owner_counts = Counter(
                    m.get("primary_owner_name") for m in metas if m.get("primary_owner_name")
                )
                most_active = max(metas, key=lambda m: m.get("commit_count_90d", 0))
                module_git_summary = {
                    "top_owners": [
                        {"name": n, "file_count": c} for n, c in owner_counts.most_common(3)
                    ],
                    "most_active_file": most_active.get("file_path", ""),
                    "most_active_commits_90d": most_active.get("commit_count_90d", 0),
                }
        user_prompt = self._render("module_page.j2", ctx=ctx, module_git_summary=module_git_summary)
        response = await self._call_provider("module_page", user_prompt, str(uuid.uuid4()))
        return self._build_generated_page(
            "module_page",
            module_path,
            f"Module: {module_path}",
            response,
            compute_source_hash(user_prompt),
            GENERATION_LEVELS["module_page"],
        )

    async def generate_scc_page(
        self,
        scc_id: str,
        scc_files: list[str],
        file_contexts: list[FilePageContext],
    ) -> GeneratedPage:
        ctx = self._assembler.assemble_scc_page(scc_id, scc_files, file_contexts)
        user_prompt = self._render("scc_page.j2", ctx=ctx)
        response = await self._call_provider("scc_page", user_prompt, str(uuid.uuid4()))
        return self._build_generated_page(
            "scc_page",
            scc_id,
            f"Circular Dependency: {scc_id}",
            response,
            compute_source_hash(user_prompt),
            GENERATION_LEVELS["scc_page"],
        )

    async def generate_repo_overview(
        self,
        repo_structure: RepoStructure,
        pagerank: dict[str, float],
        sccs: list[Any],
        community: dict[str, int],
        git_meta_map: dict[str, dict] | None = None,
        graph_builder: Any | None = None,
    ) -> GeneratedPage:
        ctx = self._assembler.assemble_repo_overview(
            repo_structure,
            pagerank,
            sccs,
            community,
            graph_builder=graph_builder,
        )
        repo_git_summary = None
        if git_meta_map:
            metas = list(git_meta_map.values())
            top_churn = sorted(metas, key=lambda m: m.get("commit_count_90d", 0), reverse=True)[:3]
            oldest = min(
                (m for m in metas if m.get("first_commit_at")),
                key=lambda m: m["first_commit_at"],
                default=None,
            )
            repo_git_summary = {
                "hotspot_count": sum(1 for m in metas if m.get("is_hotspot")),
                "stable_count": sum(1 for m in metas if m.get("is_stable")),
                "top_churn_files": [m.get("file_path", "") for m in top_churn],
                "oldest_file": oldest.get("file_path", "") if oldest else "",
                "oldest_file_age_days": oldest.get("age_days", 0) if oldest else 0,
            }
        user_prompt = self._render("repo_overview.j2", ctx=ctx, repo_git_summary=repo_git_summary)
        response = await self._call_provider("repo_overview", user_prompt, str(uuid.uuid4()))
        repo_name = getattr(repo_structure, "name", "repo")
        return self._build_generated_page(
            "repo_overview",
            repo_name,
            f"Repository Overview: {repo_name}",
            response,
            compute_source_hash(user_prompt),
            GENERATION_LEVELS["repo_overview"],
        )

    async def generate_architecture_diagram(
        self,
        graph: Any,
        pagerank: dict[str, float],
        community: dict[str, int],
        sccs: list[Any],
        repo_name: str,
    ) -> GeneratedPage:
        ctx = self._assembler.assemble_architecture_diagram(
            graph, pagerank, community, sccs, repo_name
        )
        user_prompt = self._render("architecture_diagram.j2", ctx=ctx)
        response = await self._call_provider("architecture_diagram", user_prompt, str(uuid.uuid4()))
        return self._build_generated_page(
            "architecture_diagram",
            repo_name,
            f"Architecture Diagram: {repo_name}",
            response,
            compute_source_hash(user_prompt),
            GENERATION_LEVELS["architecture_diagram"],
        )

    async def generate_api_contract(
        self,
        parsed: ParsedFile,
        source_bytes: bytes,
    ) -> GeneratedPage:
        ctx = self._assembler.assemble_api_contract(parsed, source_bytes)
        user_prompt = self._render("api_contract.j2", ctx=ctx)
        response = await self._call_provider("api_contract", user_prompt, str(uuid.uuid4()))
        return self._build_generated_page(
            "api_contract",
            parsed.file_info.path,
            f"API Contract: {parsed.file_info.path}",
            response,
            compute_source_hash(user_prompt),
            GENERATION_LEVELS["api_contract"],
        )

    async def generate_infra_page(
        self,
        parsed: ParsedFile,
        source_bytes: bytes,
    ) -> GeneratedPage:
        ctx = self._assembler.assemble_infra_page(parsed, source_bytes)
        user_prompt = self._render("infra_page.j2", ctx=ctx)
        response = await self._call_provider("infra_page", user_prompt, str(uuid.uuid4()))
        return self._build_generated_page(
            "infra_page",
            parsed.file_info.path,
            f"Infrastructure: {parsed.file_info.path}",
            response,
            compute_source_hash(user_prompt),
            GENERATION_LEVELS["infra_page"],
        )

    async def generate_cross_package(
        self,
        source_pkg: str,
        target_pkg: str,
        source_fcs: list[FilePageContext],
        target_fcs: list[FilePageContext],
        graph: Any,
    ) -> GeneratedPage:
        ctx = self._assembler.assemble_cross_package(
            source_pkg, target_pkg, source_fcs, target_fcs, graph
        )
        user_prompt = self._render("cross_package.j2", ctx=ctx)
        response = await self._call_provider("cross_package", user_prompt, str(uuid.uuid4()))
        return self._build_generated_page(
            "cross_package",
            f"{source_pkg}->{target_pkg}",
            f"Cross-Package: {source_pkg} → {target_pkg}",
            response,
            compute_source_hash(user_prompt),
            GENERATION_LEVELS["cross_package"],
        )

    # ------------------------------------------------------------------
    # generate_all — orchestration
    # ------------------------------------------------------------------

    async def generate_all(
        self,
        parsed_files: list[ParsedFile],
        source_map: dict[str, bytes],
        graph_builder: Any,  # GraphBuilder
        repo_structure: RepoStructure,
        repo_name: str,
        job_system: Any | None = None,  # JobSystem | None
        on_page_done: Callable[[str], None] | None = None,
        on_total_known: Callable[[int], None] | None = None,
        git_meta_map: dict[str, dict] | None = None,
        resume: bool = False,
        repo_path: Path | str | None = None,
    ) -> list[GeneratedPage]:
        """Generate all wiki pages for a repository.

        Runs generation in 8 ordered levels.  Each level's pages are generated
        concurrently (up to config.max_concurrency).  Failures within a level
        are logged but do not abort the remaining levels.

        Args:
            parsed_files:   All ParsedFile objects from the ingestion pipeline.
            source_map:     Raw file bytes keyed by relative path.
            graph_builder:  Finalized GraphBuilder (build() already called).
            repo_structure: High-level repo metadata.
            repo_name:      Human-readable repository name.
            job_system:     Optional JobSystem for checkpoint persistence.
            on_page_done:   Optional callback per completed page.
            git_meta_map:   Optional dict of git metadata by file path.

        Returns:
            List of GeneratedPage objects in level order.
        """
        graph = graph_builder.graph()
        pagerank = graph_builder.pagerank()
        betweenness = graph_builder.betweenness_centrality()
        community = graph_builder.community_detection()
        sccs = graph_builder.strongly_connected_components()

        all_pages: list[GeneratedPage] = []
        semaphore = asyncio.Semaphore(self._config.max_concurrency)
        # Summaries of completed pages: target_path → brief summary text (for dep context)
        completed_page_summaries: dict[str, str] = {}

        def _extract_summary(content: str) -> str:
            if "## Overview" in content:
                start = content.index("## Overview") + len("## Overview")
                end = content.find("\n##", start)
                return content[start : end if end > 0 else start + 1600].strip()[:400]
            return content[:400]

        # Determine already-completed pages (for resume support)
        completed_ids: set[str] = set()
        job_id: str | None = None
        if job_system is not None:
            repo_path_str = (
                str(Path(repo_path).resolve())
                if repo_path
                else str(getattr(repo_structure, "root_path", "."))
            )
            # On resume, query the vector store directly — it is the ground truth
            if resume and self._vector_store is not None:
                completed_ids = await self._vector_store.list_page_ids()
                if completed_ids:
                    log.info(
                        "Resuming generation from vector store",
                        already_completed=len(completed_ids),
                    )
            job_id = job_system.create_job(
                repo_path_str,
                self._config,
                self._provider.provider_name,
                self._provider.model_name,
            )

        async def run_level(named_coros: list[tuple[str, Any]], level: int) -> list[GeneratedPage]:
            if job_system is not None and job_id is not None:
                job_system.update_level(job_id, level)

            async def guarded_named(page_id: str, coro: Any) -> Any:
                async with semaphore:
                    try:
                        result = await coro
                        # Embed page for RAG (B1)
                        if self._vector_store is not None and isinstance(result, GeneratedPage):
                            try:
                                page_summary = _extract_summary(result.content)
                                await self._vector_store.embed_and_upsert(
                                    result.page_id,
                                    result.content,
                                    {
                                        "page_type": result.page_type,
                                        "target_path": result.target_path,
                                        "content": result.content[:600],
                                        "summary": page_summary,
                                    },
                                )
                            except Exception as e:
                                log.debug("rag.embed_failed", page_id=result.page_id, error=str(e))
                        # Store summary for dependency context (B2)
                        if isinstance(result, GeneratedPage):
                            completed_page_summaries[result.target_path] = _extract_summary(
                                result.content
                            )
                            # Report progress immediately (not batched after gather)
                            if on_page_done is not None:
                                on_page_done(result.page_type)
                        return result
                    except Exception as exc:
                        if job_system is not None and job_id is not None:
                            job_system.fail_page(job_id, page_id, str(exc))
                        log.error(
                            "page_generation_failed",
                            page_id=page_id,
                            level=level,
                            error=str(exc),
                        )
                        return exc  # return as value so gather works

            tasks = [guarded_named(pid, c) for pid, c in named_coros]
            results = await asyncio.gather(*tasks)
            pages = [r for r in results if isinstance(r, GeneratedPage)]
            if job_system is not None and job_id is not None:
                for r in pages:
                    job_system.complete_page(job_id, r.page_id)
            return pages

        # ---- Budget pre-computation ----
        code_files = [
            p
            for p in parsed_files
            if not p.file_info.is_api_contract
            and not _is_infra_file(p)
            and p.file_info.language in _CODE_LANGUAGES
        ]

        # Sort: entry points first, then hotspots, then high PageRank (A1)
        if git_meta_map:
            code_files = sorted(
                code_files,
                key=lambda p: (
                    not p.file_info.is_entry_point,
                    not git_meta_map.get(p.file_info.path, {}).get("is_hotspot", False),
                    -pagerank.get(p.file_info.path, 0.0),
                ),
            )
        else:
            code_files = sorted(
                code_files,
                key=lambda p: (
                    not p.file_info.is_entry_point,
                    -pagerank.get(p.file_info.path, 0.0),
                ),
            )

        code_pr_scores = sorted(
            [pagerank.get(p.file_info.path, 0.0) for p in code_files],
            reverse=True,
        )
        _all_public_symbols: list[tuple[Any, Any]] = [
            (sym, p) for p in parsed_files for sym in p.symbols if sym.visibility == "public"
        ]

        budget = max(50, int(len(parsed_files) * self._config.max_pages_pct))
        # Estimate fixed overhead (api, scc, module, repo_overview, arch_diagram)
        _fixed_overhead = (
            sum(1 for p in parsed_files if p.file_info.is_api_contract)
            + sum(1 for scc in sccs if len(scc) > 1)
            + len(
                {
                    (
                        Path(p.file_info.path).parts[0]
                        if len(Path(p.file_info.path).parts) > 1
                        else "root"
                    )
                    for p in code_files
                }
            )
            + 2  # repo_overview + architecture_diagram
        )
        _remaining = max(0, budget - _fixed_overhead)

        # File page gets priority over symbol_spotlight
        _n_file_uncapped = (
            max(1, int(len(code_pr_scores) * self._config.file_page_top_percentile))
            if code_pr_scores
            else 0
        )
        _n_file_cap = min(_n_file_uncapped, _remaining)
        pr_threshold = (
            code_pr_scores[_n_file_cap - 1] if code_pr_scores and _n_file_cap > 0 else 0.0
        )

        _sym_budget = max(0, _remaining - _n_file_cap)
        _n_sym_uncapped = (
            max(1, int(len(_all_public_symbols) * self._config.top_symbol_percentile))
            if _all_public_symbols
            else 0
        )
        _n_sym_cap = min(_n_sym_uncapped, _sym_budget)

        # Compute estimated total and notify progress (A7)
        # Use the actual file_page count (files passing _is_significant_file), not
        # _n_file_cap. The cap sets pr_threshold, but files with high betweenness or
        # entry_point status bypass that threshold, so actual count > _n_file_cap.
        _actual_file_page_count = sum(
            1
            for p in code_files
            if _is_significant_file(p, pagerank, betweenness, self._config, pr_threshold)
        )
        estimated_total = (
            sum(1 for p in parsed_files if p.file_info.is_api_contract)
            + _n_sym_cap
            + _actual_file_page_count
            + sum(1 for scc in sccs if len(scc) > 1)
            + len(
                {
                    (
                        Path(p.file_info.path).parts[0]
                        if len(Path(p.file_info.path).parts) > 1
                        else "root"
                    )
                    for p in code_files
                }
            )
            + 2  # repo_overview + arch_diagram
            + sum(1 for p in parsed_files if _is_infra_file(p))
        )
        remaining_total = max(0, estimated_total - len(completed_ids))
        if on_total_known is not None:
            on_total_known(remaining_total)
        if job_system is not None and job_id is not None:
            job_system.start_job(job_id, estimated_total)

        # ---- Level 0: api_contract ----
        api_files = [p for p in parsed_files if p.file_info.is_api_contract]
        level0_coros = [
            (
                compute_page_id("api_contract", p.file_info.path),
                self.generate_api_contract(p, source_map.get(p.file_info.path, b"")),
            )
            for p in api_files
            if compute_page_id("api_contract", p.file_info.path) not in completed_ids
        ]
        level0_pages = await run_level(level0_coros, 0)
        all_pages.extend(level0_pages)

        # ---- Level 1: symbol_spotlight (top percentile by PageRank) ----
        all_symbols_with_file: list[tuple[Any, ParsedFile]] = _all_public_symbols

        if all_symbols_with_file and _n_sym_cap > 0:
            all_symbols_with_file.sort(
                key=lambda x: pagerank.get(x[1].file_info.path, 0.0), reverse=True
            )
            top_symbols = all_symbols_with_file[:_n_sym_cap]
        else:
            top_symbols = []

        level1_coros = [
            (
                compute_page_id("symbol_spotlight", f"{pf.file_info.path}::{sym.name}"),
                self.generate_symbol_spotlight(sym, pf, pagerank, graph, source_map=source_map),
            )
            for sym, pf in top_symbols
            if compute_page_id("symbol_spotlight", f"{pf.file_info.path}::{sym.name}")
            not in completed_ids
        ]
        level1_pages = await run_level(level1_coros, 1)
        all_pages.extend(level1_pages)

        # ---- Level 2: file_page (significant code files only) ----
        # Context is assembled for ALL code files (module pages need it).
        # Pages are generated only for files that cross the significance bar.
        # page_summaries from level 0+1 are available here (B2).
        #
        # Topo-sort: process leaves (no internal out-edges) before roots so that
        # dependency summaries are available when assembling dependents' contexts.
        # Falls back to existing priority order if networkx is unavailable or graph
        # has cycles.
        code_file_paths = [p.file_info.path for p in code_files]
        try:
            import networkx as nx  # type: ignore[import]

            # Build a subgraph of just the code files we are about to generate
            code_file_set = set(code_file_paths)
            dag = nx.DiGraph()
            dag.add_nodes_from(code_file_paths)
            for path_ in code_file_paths:
                if path_ in graph:
                    for succ in graph.successors(path_):
                        if succ in code_file_set:
                            dag.add_edge(path_, succ)  # path_ depends on succ

            if nx.is_directed_acyclic_graph(dag):
                # topological_sort yields nodes in an order where for each edge u→v,
                # u comes before v — i.e. dependents before dependencies.
                # We want leaves (dependencies) first, so reverse the order.
                topo_order = list(reversed(list(nx.topological_sort(dag))))
            else:
                # Cycle present: condense SCCs, topo-sort condensation, then expand.
                condensation = nx.condensation(dag)
                topo_order_scc = list(reversed(list(nx.topological_sort(condensation))))
                scc_members: dict[int, list[str]] = {
                    n: list(condensation.nodes[n]["members"]) for n in condensation.nodes
                }
                topo_order = [node for scc_id in topo_order_scc for node in scc_members[scc_id]]

            # Preserve priority ordering within the topo-sort by mapping paths to
            # their original priority index.
            priority_index = {p: i for i, p in enumerate(code_file_paths)}
            topo_order = [p for p in topo_order if p in priority_index]
            # Re-sort code_files to match topo_order
            path_to_parsed = {p.file_info.path: p for p in code_files}
            code_files = [path_to_parsed[p] for p in topo_order if p in path_to_parsed]
        except Exception:
            pass  # Keep existing priority order on any failure

        file_page_contexts: dict[str, FilePageContext] = {}

        level2_coros: list[tuple[str, Any]] = []
        for p in code_files:
            # Pre-fetch dependency summaries from vector store for deps not yet
            # in the completed_page_summaries accumulator (e.g. from prior runs).
            if self._vector_store is not None:
                path_ = p.file_info.path
                out_edges = list(graph.successors(path_)) if path_ in graph else []
                internal_deps = [e for e in out_edges if not e.startswith("external:")]
                for dep in internal_deps:
                    if dep not in completed_page_summaries:
                        try:
                            result = await self._vector_store.get_page_summary_by_path(dep)
                            if result and result.get("summary"):
                                completed_page_summaries[dep] = result["summary"]
                        except Exception:
                            pass  # Non-fatal — dep context is optional

            ctx = self._assembler.assemble_file_page(
                p,
                graph,
                pagerank,
                betweenness,
                community,
                source_map.get(p.file_info.path, b""),
                git_meta=git_meta_map.get(p.file_info.path) if git_meta_map else None,
                page_summaries=completed_page_summaries,
            )
            file_page_contexts[p.file_info.path] = ctx
            pid = compute_page_id("file_page", p.file_info.path)
            if (
                _is_significant_file(p, pagerank, betweenness, self._config, pr_threshold)
                and pid not in completed_ids
            ):
                level2_coros.append((pid, self._generate_file_page_from_ctx(p, ctx)))

        level2_pages = await run_level(level2_coros, 2)
        all_pages.extend(level2_pages)

        # ---- Level 3: scc_page (only true cycles: len > 1) ----
        scc_coros: list[tuple[str, Any]] = []
        for i, scc in enumerate(sccs):
            if len(scc) <= 1:
                continue
            scc_id = f"scc-{i}"
            scc_files = sorted(scc)
            fc_list = [file_page_contexts[f] for f in scc_files if f in file_page_contexts]
            pid = compute_page_id("scc_page", scc_id)
            if pid not in completed_ids:
                scc_coros.append((pid, self.generate_scc_page(scc_id, scc_files, fc_list)))
        level3_pages = await run_level(scc_coros, 3)
        all_pages.extend(level3_pages)

        # ---- Level 4: module_page (grouped by top-level directory) ----
        module_groups: dict[str, list[FilePageContext]] = {}
        module_languages: dict[str, str] = {}
        for p in code_files:
            parts = Path(p.file_info.path).parts
            module = parts[0] if len(parts) > 1 else "root"
            fc = file_page_contexts.get(p.file_info.path)
            if fc is not None:
                module_groups.setdefault(module, []).append(fc)
                module_languages[module] = p.file_info.language

        level4_coros: list[tuple[str, Any]] = [
            (
                compute_page_id("module_page", module),
                self.generate_module_page(
                    module,
                    module_languages.get(module, "unknown"),
                    fcs,
                    graph,
                    git_meta_map=git_meta_map,
                    page_summaries=completed_page_summaries,
                ),
            )
            for module, fcs in module_groups.items()
            if compute_page_id("module_page", module) not in completed_ids
        ]
        level4_pages = await run_level(level4_coros, 4)
        all_pages.extend(level4_pages)

        # ---- Level 5: cross_package (only if monorepo) ----
        if repo_structure.is_monorepo:
            seen_pairs: set[tuple[str, str]] = set()
            cross_coros: list[tuple[str, Any]] = []
            for src_pkg, src_fcs in module_groups.items():
                for fc in src_fcs:
                    for dep in fc.dependencies:
                        dep_parts = Path(dep).parts
                        dep_pkg = dep_parts[0] if len(dep_parts) > 1 else "root"
                        pair = (src_pkg, dep_pkg)
                        if dep_pkg != src_pkg and pair not in seen_pairs:
                            seen_pairs.add(pair)
                            dep_fcs = module_groups.get(dep_pkg, [])
                            ctx_xpkg = self._assembler.assemble_cross_package(
                                src_pkg, dep_pkg, src_fcs, dep_fcs, graph
                            )
                            if ctx_xpkg.coupling_strength >= 2:
                                pid = compute_page_id("cross_package", f"{src_pkg}->{dep_pkg}")
                                if pid not in completed_ids:
                                    cross_coros.append(
                                        (
                                            pid,
                                            self.generate_cross_package(
                                                src_pkg, dep_pkg, src_fcs, dep_fcs, graph
                                            ),
                                        )
                                    )
            level5_pages = await run_level(cross_coros, 5)
            all_pages.extend(level5_pages)

        # ---- Level 6: repo_overview + architecture_diagram ----
        level6_coros: list[tuple[str, Any]] = []
        if compute_page_id("repo_overview", repo_name) not in completed_ids:
            level6_coros.append(
                (
                    compute_page_id("repo_overview", repo_name),
                    self.generate_repo_overview(
                        repo_structure,
                        pagerank,
                        sccs,
                        community,
                        git_meta_map=git_meta_map,
                        graph_builder=graph_builder,
                    ),
                )
            )
        if compute_page_id("architecture_diagram", repo_name) not in completed_ids:
            level6_coros.append(
                (
                    compute_page_id("architecture_diagram", repo_name),
                    self.generate_architecture_diagram(graph, pagerank, community, sccs, repo_name),
                )
            )
        level6_pages = await run_level(level6_coros, 6)
        all_pages.extend(level6_pages)

        # ---- Level 7: infra_page ----
        infra_files = [p for p in parsed_files if _is_infra_file(p)]
        level7_coros: list[tuple[str, Any]] = [
            (
                compute_page_id("infra_page", p.file_info.path),
                self.generate_infra_page(p, source_map.get(p.file_info.path, b"")),
            )
            for p in infra_files
            if compute_page_id("infra_page", p.file_info.path) not in completed_ids
        ]
        level7_pages = await run_level(level7_coros, 7)
        all_pages.extend(level7_pages)

        # Finalize job
        if job_system is not None and job_id is not None:
            job_system.complete_job(job_id)

        log.info(
            "Generation complete",
            total_pages=len(all_pages),
            provider=self._provider.provider_name,
            model=self._provider.model_name,
        )
        return all_pages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _generate_file_page_from_ctx(
        self,
        parsed: ParsedFile,
        ctx: FilePageContext,
    ) -> GeneratedPage:
        """Generate a file_page from a pre-assembled context (avoids double-assembly)."""
        # RAG context: query vector store for related pages (B1)
        if self._vector_store is not None:
            query_terms = parsed.exports or [
                s["name"] for s in ctx.symbols[:3] if s.get("visibility") == "public"
            ]
            if query_terms:
                try:
                    results = await self._vector_store.search(", ".join(query_terms[:5]), limit=3)
                    self_id = f"file_page:{parsed.file_info.path}"
                    ctx.rag_context = [
                        f"[{r.page_id}]\n{r.snippet}" for r in results if r.page_id != self_id
                    ]
                except Exception as e:
                    log.debug("rag.search_failed", path=parsed.file_info.path, error=str(e))
        user_prompt = self._render("file_page.j2", ctx=ctx)
        response = await self._call_provider("file_page", user_prompt, str(uuid.uuid4()))
        page = self._build_generated_page(
            "file_page",
            parsed.file_info.path,
            f"File: {parsed.file_info.path}",
            response,
            compute_source_hash(user_prompt),
            GENERATION_LEVELS["file_page"],
        )
        # Cross-check LLM output against actual symbols
        hal_warnings = _validate_symbol_references(response.content, parsed)
        if hal_warnings:
            log.warning(
                "hallucination_check",
                path=parsed.file_info.path,
                count=len(hal_warnings),
                refs=hal_warnings[:5],
            )
            page.metadata["hallucination_warnings"] = hal_warnings
        return page
    
    async def _call_provider(
        self,
        page_type: str,
        user_prompt: str,
        request_id: str,
    ) -> GeneratedResponse:
        key = self._compute_cache_key(page_type, user_prompt)
        if self._config.cache_enabled and key in self._cache:
            log.debug("Cache hit", page_type=page_type, key=key[:8])
            return self._cache[key]

        base_system = SYSTEM_PROMPTS[page_type]
    
        # Validate and sanitize language
        lang_code = self._language.lower().strip() if self._language else "en"
        # Remove any newlines or control characters (prevent prompt injection)
        lang_code = ''.join(ch for ch in lang_code if ch.isalnum() or ch == '_')
        if lang_code not in _LANGUAGE_NAMES:
            log.warning(f"Unknown language code '{lang_code}', falling back to English")
            lang_code = "en"
        lang_name = _LANGUAGE_NAMES.get(lang_code, "English")
    
        if lang_code != "en":
            language_instruction = (
                f"Generate all documentation content in {lang_name}. "
                "Keep all code, file paths, and symbol names in their original form. "
                "Do not translate them.\n\n"
            )
            system_prompt = language_instruction + base_system
        else:
            system_prompt = base_system

        response = await self._provider.generate(
            system_prompt,
            user_prompt,
            max_tokens=self._config.max_tokens,
            temperature=self._config.temperature,
            request_id=request_id,
        )

        if self._config.cache_enabled:
            self._cache[key] = response

        return response
   
    def _compute_cache_key(self, page_type: str, user_prompt: str) -> str:
        """Return SHA256(model + language + page_type + user_prompt) as cache key."""
        raw = f"{self._provider.model_name}:{self._language}:{page_type}:{user_prompt}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _build_generated_page(
        self,
        page_type: str,
        target_path: str,
        title: str,
        response: GeneratedResponse,
        source_hash: str,
        level: int,
    ) -> GeneratedPage:
        """Wrap a GeneratedResponse in a GeneratedPage."""
        now = _now_iso()
        return GeneratedPage(
            page_id=compute_page_id(page_type, target_path),
            page_type=page_type,
            title=title,
            content=response.content,
            summary=_extract_summary(response.content),
            source_hash=source_hash,
            model_name=self._provider.model_name,
            provider_name=self._provider.provider_name,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cached_tokens=response.cached_tokens,
            generation_level=level,
            target_path=target_path,
            created_at=now,
            updated_at=now,
        )

    def _render(self, template_name: str, **kwargs: Any) -> str:
        """Render a Jinja2 template with the given kwargs."""
        template = self._jinja_env.get_template(template_name)
        return template.render(**kwargs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_summary(content: str, max_chars: int = 320) -> str:
    """Extract a 1–3 sentence purpose blurb from rendered wiki markdown.

    Strategy: walk lines top-to-bottom, skip blanks/headings/list-markers/HTML
    comments, and take the first prose paragraph. Truncate at sentence boundary
    near max_chars. Fully deterministic — no extra LLM call.
    """
    if not content:
        return ""
    para_lines: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            if para_lines:
                break
            continue
        if line.startswith(("#", ">", "```", "---", "<!--", "|", "- ", "* ", "1.")):
            if para_lines:
                break
            continue
        para_lines.append(line)
    if not para_lines:
        return ""
    text = " ".join(para_lines)
    if len(text) <= max_chars:
        return text
    # Truncate at the last sentence boundary before max_chars
    cut = text[:max_chars]
    last_period = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    if last_period > max_chars // 2:
        return cut[: last_period + 1]
    return cut.rstrip() + "…"


def _is_infra_file(parsed: ParsedFile) -> bool:
    """Return True if the file is an infrastructure file."""
    lang = parsed.file_info.language
    if lang in _INFRA_LANGUAGES:
        return True
    name = Path(parsed.file_info.path).name
    return name in _INFRA_FILENAMES


def _is_significant_file(
    parsed: ParsedFile,
    pagerank: dict[str, float],
    betweenness: dict[str, float],
    config: Any,  # GenerationConfig
    pr_threshold: float,
) -> bool:
    """Return True if this code file deserves its own file_page.

    A file is significant if it is connected/important in the dependency graph
    (entry point, top PageRank percentile, or bridge file) AND has enough
    content to document.

    The symbol requirement is waived for files with no original definitions
    (state modules, __init__ re-exporters, config files) that are still heavily
    imported — these are architecturally important even without function bodies.
    Package __init__.py files with any symbols are always included since they
    are the public interface of their module.
    """
    path = parsed.file_info.path
    pr = pagerank.get(path, 0.0)
    bet = betweenness.get(path, 0.0)
    is_entry = parsed.file_info.is_entry_point

    # Package __init__.py files are module interfaces — always include them
    # if they have any symbols (re-exports, __getattr__, etc.)
    if path.endswith("__init__.py") and len(parsed.symbols) > 0:
        return True

    # Test files are always significant when present. They have near-zero
    # PageRank because nothing imports them back, but they answer "what
    # tests exercise X" / "where is Y verified" questions that the doc layer
    # is the right place to surface. Users who want to exclude tests
    # entirely can do so via skip_tests in the orchestrator upstream.
    if parsed.file_info.is_test and len(parsed.symbols) > 0:
        return True

    # Must appear significant in the graph
    if not (is_entry or pr >= pr_threshold or bet > 0.0):
        return False

    # Waive the symbol-count requirement for graph-connected files that have
    # no original definitions of their own (e.g. state/config modules that
    # are imported by many files but mostly re-export or assemble values).
    if len(parsed.symbols) < config.file_page_min_symbols:
        return is_entry or pr >= pr_threshold

    return True


# ---------------------------------------------------------------------------
# LLM output validation
# ---------------------------------------------------------------------------

# Common words that appear in backticks but are not code symbols.
_BACKTICK_SKIP = frozenset(
    {
        # Python builtins & keywords
        "True",
        "False",
        "None",
        "self",
        "cls",
        "super",
        "str",
        "int",
        "float",
        "bool",
        "list",
        "dict",
        "set",
        "tuple",
        "bytes",
        "object",
        "type",
        "Any",
        "Optional",
        "Union",
        "async",
        "await",
        "return",
        "yield",
        "import",
        "from",
        "class",
        "def",
        "if",
        "else",
        "for",
        "while",
        "try",
        "except",
        "raise",
        "with",
        "pass",
        "break",
        "continue",
        "lambda",
        "in",
        "not",
        "and",
        "or",
        "is",
        "del",
        "assert",
        "finally",
        "elif",
        "as",
        "global",
        "nonlocal",
        # JS/TS keywords
        "null",
        "undefined",
        "this",
        "const",
        "let",
        "var",
        "function",
        "export",
        "default",
        "extends",
        "implements",
        "interface",
        "enum",
        "new",
        "typeof",
        "instanceof",
        "void",
        "never",
        "string",
        "number",
        "boolean",
        "symbol",
        "bigint",
        "unknown",
        "readonly",
        "abstract",
        "static",
        "private",
        "protected",
        "public",
        "require",
        "module",
        "exports",
        "Promise",
        "Map",
        "Set",
        "Array",
        "Object",
        "Error",
        "Date",
        "RegExp",
        "JSON",
        "Math",
        "console",
        # Common tool/ecosystem names
        "pip",
        "npm",
        "npx",
        "yarn",
        "pnpm",
        "go",
        "rust",
        "python",
        "node",
        "cargo",
        "uv",
        "git",
        "docker",
        "make",
        # Common framework/lib names the LLM mentions in prose
        "FastAPI",
        "React",
        "Next",
        "Express",
        "Django",
        "Flask",
        "SQLAlchemy",
        "Pydantic",
        "Click",
        "Typer",
        "pytest",
        "asyncio",
        "pathlib",
        "dataclass",
        "dataclasses",
    }
)

# Regex: single-backtick references that look like identifiers.
_BACKTICK_REF_RE = re.compile(r"(?<!`)` *([A-Za-z_]\w*(?:\.\w+)*) *`(?!`)")

# Patterns that indicate the backtick content is a path, command, or
# value rather than a symbol reference — these should never be flagged.
_PATH_OR_CMD_RE = re.compile(
    r"[/\\]"  # contains path separator
    r"|\.(?:py|ts|js|json|yaml|yml|toml|md|sh|sql|css|html)$"  # file extension
    r"|^[a-z][\w-]*$"  # all-lowercase with hyphens = CLI command/flag
)


def _validate_symbol_references(
    content: str,
    parsed: ParsedFile,
) -> list[str]:
    """Cross-check backtick-quoted names in LLM output against actual symbols.

    Returns a list of warning strings for references that don't match any
    known symbol, export, or import in the ParsedFile. Designed to have low
    false-positive rates — only flags references that look like symbol names
    but can't be found anywhere in the file's AST, imports, or source text.
    """
    refs = set(_BACKTICK_REF_RE.findall(content))
    if not refs:
        return []

    # Build the known-names set from AST data
    known: set[str] = set()
    for s in parsed.symbols:
        known.add(s.name)
        known.add(s.qualified_name)
        # Decorator names are valid references (e.g. @app.command("init"))
        for dec in s.decorators:
            # Extract the decorator function name: "@app.command" → "command"
            dec_name = dec.lstrip("@").split("(")[0]
            known.add(dec_name)
            known.add(dec_name.split(".")[-1])
    known.update(parsed.exports)
    for imp in parsed.imports:
        if imp.module_path:
            # Add both the final component and intermediate segments
            parts = imp.module_path.split(".")
            known.update(parts)
        known.update(imp.imported_names)
        # Named bindings from import resolution
        for binding in getattr(imp, "bindings", []):
            known.add(binding.local_name)
            if binding.exported_name:
                known.add(binding.exported_name)

    # Also add all string literals from the source that look like identifiers
    # (catches Click command names, decorator arguments, dict keys, etc.)
    source_text = ""
    if hasattr(parsed, "file_info") and hasattr(parsed.file_info, "path"):
        # The source is in the context, but we only have the parsed file here.
        # Use docstring and symbol names as a cheap approximation.
        if parsed.docstring:
            known.update(w for w in parsed.docstring.split() if w.isidentifier())

    warnings: list[str] = []
    for ref in refs:
        if ref in _BACKTICK_SKIP:
            continue
        # Skip short refs (1-2 chars are usually variables like `x`, `i`, `db`)
        if len(ref) <= 2:
            continue
        # Skip anything that looks like a path, file, or CLI command
        if _PATH_OR_CMD_RE.search(ref):
            continue
        # Skip all-uppercase (likely constants from other files: `MAX_RETRIES`)
        if ref.isupper():
            continue
        # Check against known names
        base = ref.split(".")[-1]

        if ref in known or base in known:
            continue
        # Skip if the ref is a substring of any known symbol (covers partial
        # references like `parse` when `parse_file` exists)
        if any(ref in k for k in known if len(k) > len(ref)):
            continue
        warnings.append(ref)
    return warnings
