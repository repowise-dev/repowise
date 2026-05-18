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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jinja2
import structlog

from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY
from repowise.core.ingestion.models import ParsedFile, RepoStructure
from repowise.core.providers.llm.base import BaseProvider, CacheHint, GeneratedResponse

from . import onboarding as _onboarding
from .context_assembler import ContextAssembler, FilePageContext
from .models import (
    GENERATION_LEVELS,
    GeneratedPage,
    GenerationConfig,
    compute_page_id,
    compute_source_hash,
)

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class PriorPage:
    """Snapshot of a previously-generated page used for cross-run reuse.

    Lives in :class:`PageGenerator` keyed by ``page_id``. When the freshly
    rendered prompt produces a matching ``source_hash`` under the same
    ``model_name``, the LLM call is skipped and ``content`` is reused.
    """

    source_hash: str
    model_name: str
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0

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
    "onboarding": (
        "You are repowise, an expert technical documentation generator producing "
        "a single page in a curated Onboarding collection that a new contributor "
        "or LLM agent reads first. "
        "Write concise, navigable prose grounded in the structured signals supplied. "
        "Do not invent file paths, symbol names, or rationale that is not in the context. "
        "Output markdown only — follow the exact section structure the user prompt prescribes."
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
        prior_pages: dict[str, "PriorPage"] | None = None,
    ) -> None:
        self._provider = provider
        self._assembler = assembler
        self._config = config
        self._vector_store = vector_store
        self._language = language
        self._cache: dict[str, GeneratedResponse] = {}
        # Map of page_id → PriorPage from previous generation runs. When the
        # rendered prompt's source_hash matches the prior page's hash AND the
        # model is the same, the LLM call is skipped and the prior content is
        # reused. Wired by the orchestrator from the persisted wiki_pages
        # table.
        self._prior_pages: dict[str, PriorPage] = prior_pages or {}
        self._reuse_count: int = 0

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
        response = await self._call_provider(
            "file_page", user_prompt, str(uuid.uuid4()), target_path=parsed.file_info.path
        )
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
        target = f"{parsed.file_info.path}::{symbol.name}"
        response = await self._call_provider(
            "symbol_spotlight", user_prompt, str(uuid.uuid4()), target_path=target
        )
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
        decision_records: list[dict] | None = None,
        dead_code_findings: list[dict] | None = None,
        external_systems: list[dict] | None = None,
        community_label: str | None = None,
        community_cohesion: float | None = None,
        target_path: str | None = None,
    ) -> GeneratedPage:
        ctx = self._assembler.assemble_module_page(
            module_path,
            language,
            file_contexts,
            graph,
            page_summaries=page_summaries,
            git_meta_map=git_meta_map,
            decision_records=decision_records,
            dead_code_findings=dead_code_findings,
            external_systems=external_systems,
            community_label=community_label,
            community_cohesion=community_cohesion,
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
        page_target = target_path or module_path
        response = await self._call_provider(
            "module_page", user_prompt, str(uuid.uuid4()), target_path=page_target
        )
        return self._build_generated_page(
            "module_page",
            page_target,
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
        response = await self._call_provider(
            "scc_page", user_prompt, str(uuid.uuid4()), target_path=scc_id
        )
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
        repo_name: str | None = None,
        external_systems: list[dict] | None = None,
        decision_records: list[dict] | None = None,
    ) -> GeneratedPage:
        ctx = self._assembler.assemble_repo_overview(
            repo_structure,
            pagerank,
            sccs,
            community,
            graph_builder=graph_builder,
            external_systems=external_systems,
            decision_records=decision_records,
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
        if not repo_name:
            repo_name = getattr(repo_structure, "name", None) or "repo"
        user_prompt = self._render("repo_overview.j2", ctx=ctx, repo_git_summary=repo_git_summary)
        response = await self._call_provider(
            "repo_overview", user_prompt, str(uuid.uuid4()), target_path=repo_name
        )
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
        response = await self._call_provider(
            "architecture_diagram", user_prompt, str(uuid.uuid4()), target_path=repo_name
        )
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
        response = await self._call_provider(
            "api_contract", user_prompt, str(uuid.uuid4()), target_path=parsed.file_info.path
        )
        return self._build_generated_page(
            "api_contract",
            parsed.file_info.path,
            f"API Contract: {parsed.file_info.path}",
            response,
            compute_source_hash(user_prompt),
            GENERATION_LEVELS["api_contract"],
        )

    async def generate_onboarding_page(
        self,
        spec: "_onboarding.SubkindSpec",
        signals: "_onboarding.OnboardingSignals",
    ) -> GeneratedPage | None:
        """Generate one onboarding page from a registered subkind spec.

        Returns ``None`` when the subkind's gate fails (``build_context``
        returned ``None``) — the slot is silently skipped for this repo.
        """
        ctx = spec.build_context(signals)
        if ctx is None:
            log.debug("onboarding.gate_skipped", slot=spec.slot)
            return None

        template_name = f"onboarding/{spec.template}"
        user_prompt = self._render(template_name, ctx=ctx, slot=spec.slot)
        target = _onboarding.target_path(spec.slot)
        response = await self._call_provider(
            "onboarding", user_prompt, str(uuid.uuid4()), target_path=target
        )
        page = self._build_generated_page(
            "onboarding",
            target,
            spec.title,
            response,
            compute_source_hash(user_prompt),
            GENERATION_LEVELS["onboarding"],
        )
        # Subkind discriminator lives in metadata; page_type alone is shared
        # across all six generated onboarding slots.
        page.metadata["subkind"] = spec.slot
        page.metadata["onboarding_slot"] = spec.slot
        return page

    @staticmethod
    def _tag_promoted_pages(pages: list[GeneratedPage]) -> None:
        """Tag repo_overview / architecture_diagram pages with their slot.

        Mutates each matching page's ``metadata["onboarding_slot"]`` so the
        UI groups them into the Onboarding folder without changing their
        underlying ``page_type``. Idempotent and tolerant of missing pages.
        """
        for page in pages:
            slot = _onboarding.PROMOTED_SLOTS.get(page.page_type)
            if slot is not None:
                page.metadata["onboarding_slot"] = slot

    async def generate_infra_page(
        self,
        parsed: ParsedFile,
        source_bytes: bytes,
    ) -> GeneratedPage:
        ctx = self._assembler.assemble_infra_page(parsed, source_bytes)
        user_prompt = self._render("infra_page.j2", ctx=ctx)
        response = await self._call_provider(
            "infra_page", user_prompt, str(uuid.uuid4()), target_path=parsed.file_info.path
        )
        return self._build_generated_page(
            "infra_page",
            parsed.file_info.path,
            f"Infrastructure: {parsed.file_info.path}",
            response,
            compute_source_hash(user_prompt),
            GENERATION_LEVELS["infra_page"],
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
        on_subphase: Callable[[str, int | None], None] | None = None,
        git_meta_map: dict[str, dict] | None = None,
        resume: bool = False,
        repo_path: Path | str | None = None,
        dead_code_report: Any | None = None,
        decision_report: Any | None = None,
        external_systems: list[dict] | None = None,
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

        # ---- Build per-file lookup maps from phase-2 signals ----
        dead_code_by_file: dict[str, list[dict]] = {}
        if dead_code_report is not None and getattr(dead_code_report, "findings", None):
            for f in dead_code_report.findings:
                dead_code_by_file.setdefault(f.file_path, []).append(
                    {
                        "symbol_name": f.symbol_name,
                        "symbol_kind": f.symbol_kind,
                        "kind": str(f.kind),
                        "reason": f.reason,
                        "confidence": f.confidence,
                        "safe_to_delete": f.safe_to_delete,
                    }
                )

        decisions_by_file: dict[str, list[dict]] = {}
        decisions_all: list[dict] = []
        if decision_report is not None and getattr(decision_report, "decisions", None):
            for d in decision_report.decisions:
                payload = {
                    "title": d.title,
                    "decision": d.decision,
                    "rationale": d.rationale,
                    "source": d.source,
                    "confidence": d.confidence,
                    "evidence_file": d.evidence_file,
                }
                decisions_all.append(payload)
                for fp in d.affected_files or []:
                    decisions_by_file.setdefault(fp, []).append(payload)

        external_systems = external_systems or []

        all_pages: list[GeneratedPage] = []
        semaphore = asyncio.Semaphore(self._config.max_concurrency)
        embed_semaphore = asyncio.Semaphore(self._config.embed_concurrency or 1)
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

        async def _embed_async(page: GeneratedPage) -> None:
            """Embed a finished page into the vector store. Safe to fire-and-forget.

            Errors are swallowed at debug level — embedding is a RAG
            enhancement, not load-bearing for the page itself.
            """
            try:
                summary = _extract_summary(page.content)
                async with embed_semaphore:
                    await self._vector_store.embed_and_upsert(
                        page.page_id,
                        page.content,
                        {
                            "page_type": page.page_type,
                            "target_path": page.target_path,
                            "content": page.content[:600],
                            "summary": summary,
                        },
                    )
            except Exception as e:
                log.debug("rag.embed_failed", page_id=page.page_id, error=str(e))

        async def run_level(named_coros: list[tuple[str, Any]], level: int) -> list[GeneratedPage]:
            if job_system is not None and job_id is not None:
                job_system.update_level(job_id, level)

            # Embed tasks spawned during this level. Drained after the
            # gather() so the next level's RAG search sees a fully-indexed
            # store, but never blocks the per-page wave.
            pending_embeds: list[asyncio.Task[None]] = []

            async def guarded_named(page_id: str, coro: Any) -> Any:
                try:
                    async with semaphore:
                        result = await coro

                    if isinstance(result, GeneratedPage):
                        # Summary capture is cheap (string ops) — keep
                        # inline so the next page's context assembly sees
                        # it immediately.
                        completed_page_summaries[result.target_path] = _extract_summary(
                            result.content
                        )
                        # Progress tick fires the moment the LLM call
                        # returns — the user sees the page as done before
                        # the embed completes.
                        if on_page_done is not None:
                            on_page_done(result.page_type)
                        # Fire-and-forget the embed/upsert. Removes ~1
                        # embedder round-trip from this task's critical
                        # path so the LLM slot frees up immediately.
                        if self._vector_store is not None:
                            pending_embeds.append(
                                asyncio.create_task(_embed_async(result))
                            )
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
            # Drain pending embeds before declaring the level done — the
            # next level's RAG search depends on these landing in the store.
            if pending_embeds:
                await asyncio.gather(*pending_embeds, return_exceptions=True)
            pages = [r for r in results if isinstance(r, GeneratedPage)]
            if job_system is not None and job_id is not None:
                for r in pages:
                    job_system.complete_page(job_id, r.page_id)
            return pages

        # ---- Page selection (single source of truth) ----
        # The selection subsystem scores every candidate, allocates the
        # global budget across page-type buckets, and returns an
        # allow-set. Each level's emit loop iterates only over members
        # of that allow-set — there are no bypass paths. The same
        # function is called by the cost estimator so the pre-run
        # estimate cannot drift from the actual run.
        code_files = [
            p
            for p in parsed_files
            if not p.file_info.is_api_contract
            and not _is_infra_file(p)
            and p.file_info.language in _CODE_LANGUAGES
        ]

        # Near-clone dedupe runs before scoring so clone losers never
        # consume scoring budget. Entry points are never dropped.
        if getattr(self._config, "dedupe_near_clones", True):
            drop_paths = _select_clone_representatives(code_files, pagerank)
            if drop_paths:
                log.info("page_selection.clone_dedupe", dropped=len(drop_paths))
                code_files = [p for p in code_files if p.file_info.path not in drop_paths]
                parsed_files_for_selection = [
                    p for p in parsed_files if p.file_info.path not in drop_paths
                ]
            else:
                parsed_files_for_selection = parsed_files
        else:
            parsed_files_for_selection = parsed_files

        try:
            community_info_map = graph_builder.community_info() or {}
        except Exception:
            community_info_map = {}

        from .selection import SelectionInputs, select_pages

        selection = select_pages(
            SelectionInputs(
                parsed_files=parsed_files_for_selection,
                pagerank=pagerank,
                betweenness=betweenness,
                community=community,
                community_info=community_info_map,
                sccs=list(sccs),
                git_meta_map=git_meta_map,
                config=self._config,
            )
        )

        # Bucket allow-sets — O(1) membership checks in the level loops.
        sel_file_paths: set[str] = set(selection.file_page_paths)
        sel_symbol_keys: set[tuple[str, str]] = set(selection.symbol_spotlights)
        sel_api_paths: set[str] = set(selection.api_contract_paths)
        sel_infra_paths: set[str] = set(selection.infra_paths)
        sel_module_groups = list(selection.module_groups)
        sel_scc_groups = list(selection.scc_groups)

        # Sort code_files for stable level-2 ordering: selected files
        # first (so dep summaries land in the store earliest), then by
        # PageRank desc. The topo-sort below further refines this.
        code_files = sorted(
            code_files,
            key=lambda p: (
                p.file_info.path not in sel_file_paths,
                not p.file_info.is_entry_point,
                -pagerank.get(p.file_info.path, 0.0),
            ),
        )

        estimated_total = (
            selection.counts()["api_contract"]
            + selection.counts()["symbol_spotlight"]
            + selection.counts()["file_page"]
            + selection.counts()["scc_page"]
            + selection.counts()["module_page"]
            + int(selection.emit_repo_overview)
            + int(selection.emit_arch_diagram)
            + selection.counts()["infra_page"]
        )
        remaining_total = max(0, estimated_total - len(completed_ids))
        if on_total_known is not None:
            on_total_known(remaining_total)
        if job_system is not None and job_id is not None:
            job_system.start_job(job_id, estimated_total)

        # ---- Level 0: api_contract (allow-set filtered) ----
        api_files = [
            p
            for p in parsed_files
            if p.file_info.is_api_contract and p.file_info.path in sel_api_paths
        ]
        level0_coros = [
            (
                compute_page_id("api_contract", p.file_info.path),
                self.generate_api_contract(p, source_map.get(p.file_info.path, b"")),
            )
            for p in api_files
            if compute_page_id("api_contract", p.file_info.path) not in completed_ids
        ]
        # ---- Level 1: symbol_spotlight (allow-set filtered) ----
        # The selection layer already picked the top symbols by score;
        # here we just resolve them back to (Symbol, ParsedFile) pairs.
        parsed_by_path: dict[str, ParsedFile] = {
            p.file_info.path: p for p in parsed_files
        }
        top_symbols: list[tuple[Any, ParsedFile]] = []
        for file_path, sym_name in selection.symbol_spotlights:
            pf = parsed_by_path.get(file_path)
            if pf is None:
                continue
            sym = next((s for s in pf.symbols if s.name == sym_name), None)
            if sym is not None:
                top_symbols.append((sym, pf))

        level1_coros = [
            (
                compute_page_id("symbol_spotlight", f"{pf.file_info.path}::{sym.name}"),
                self.generate_symbol_spotlight(sym, pf, pagerank, graph, source_map=source_map),
            )
            for sym, pf in top_symbols
            if compute_page_id("symbol_spotlight", f"{pf.file_info.path}::{sym.name}")
            not in completed_ids
        ]

        # Levels 0 (api_contract) and 1 (symbol_spotlight) share no
        # data dependencies — both feed into nothing else upstream of
        # Level 2 — so they run in one merged batch instead of two
        # sequential barriers. ``run_level`` already bounds total
        # concurrency via ``self._config.max_concurrency`` so the merge
        # only removes idle slots, never over-saturates the provider.
        level01_pages = await run_level(level0_coros + level1_coros, 1)
        all_pages.extend(level01_pages)

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

        # Batch-prefetch dependency summaries from the vector store in a
        # SINGLE call covering every code file's dependencies — replaces
        # the prior per-file serial loop that turned N×M awaits into a
        # measurable bottleneck on the level-2 critical path.
        if self._vector_store is not None:
            needed_deps: set[str] = set()
            for p in code_files:
                path_ = p.file_info.path
                if path_ not in graph:
                    continue
                for dep in graph.successors(path_):
                    if dep.startswith("external:"):
                        continue
                    if dep in completed_page_summaries:
                        continue
                    needed_deps.add(dep)
            if needed_deps:
                try:
                    batch = await self._vector_store.get_page_summaries_by_paths(
                        list(needed_deps)
                    )
                    for dep_path, payload in batch.items():
                        summary = payload.get("summary") if payload else None
                        if summary:
                            completed_page_summaries[dep_path] = summary
                except Exception as exc:
                    log.debug("rag.batch_dep_prefetch_failed", error=str(exc))

        level2_coros: list[tuple[str, Any]] = []
        for p in code_files:
            ctx = self._assembler.assemble_file_page(
                p,
                graph,
                pagerank,
                betweenness,
                community,
                source_map.get(p.file_info.path, b""),
                git_meta=git_meta_map.get(p.file_info.path) if git_meta_map else None,
                page_summaries=completed_page_summaries,
                dead_code_findings=dead_code_by_file.get(p.file_info.path),
                decision_records=decisions_by_file.get(p.file_info.path),
            )
            file_page_contexts[p.file_info.path] = ctx
            pid = compute_page_id("file_page", p.file_info.path)
            if p.file_info.path in sel_file_paths and pid not in completed_ids:
                level2_coros.append((pid, self._generate_file_page_from_ctx(p, ctx)))

        level2_pages = await run_level(level2_coros, 2)
        all_pages.extend(level2_pages)

        # ---- Level 3: scc_page (allow-set filtered) ----
        scc_coros: list[tuple[str, Any]] = []
        for scc_id, scc_files in sel_scc_groups:
            fc_list = [file_page_contexts[f] for f in scc_files if f in file_page_contexts]
            pid = compute_page_id("scc_page", scc_id)
            if pid not in completed_ids:
                scc_coros.append((pid, self.generate_scc_page(scc_id, scc_files, fc_list)))
        level3_pages = await run_level(scc_coros, 3)
        all_pages.extend(level3_pages)

        # ---- Level 4: module_page (allow-set filtered) ----
        # Module groups come from the selection layer; we only need to
        # resolve FilePageContext objects for the files we already
        # built contexts for in Level 2.
        level4_coros: list[tuple[str, Any]] = []
        for mg in sel_module_groups:
            fcs = [
                file_page_contexts[fp]
                for fp in mg.file_paths
                if fp in file_page_contexts
            ]
            if not fcs:
                continue
            page_id = compute_page_id("module_page", mg.key)
            if page_id in completed_ids:
                continue
            level4_coros.append(
                (
                    page_id,
                    self.generate_module_page(
                        mg.display,
                        mg.language,
                        fcs,
                        graph,
                        git_meta_map=git_meta_map,
                        page_summaries=completed_page_summaries,
                        decision_records=decisions_all,
                        dead_code_findings=[
                            d for fc in fcs for d in dead_code_by_file.get(fc.file_path, [])
                        ],
                        external_systems=external_systems,
                        community_label=mg.label,
                        community_cohesion=mg.cohesion,
                        target_path=mg.key,
                    ),
                )
            )
        level4_pages = await run_level(level4_coros, 4)
        all_pages.extend(level4_pages)

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
                        repo_name=repo_name,
                        external_systems=external_systems,
                        decision_records=decisions_all[:10],
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
        # ---- Level 7: infra_page (allow-set filtered) ----
        infra_files = [
            p
            for p in parsed_files
            if _is_infra_file(p) and p.file_info.path in sel_infra_paths
        ]
        level7_coros: list[tuple[str, Any]] = [
            (
                compute_page_id("infra_page", p.file_info.path),
                self.generate_infra_page(p, source_map.get(p.file_info.path, b"")),
            )
            for p in infra_files
            if compute_page_id("infra_page", p.file_info.path) not in completed_ids
        ]

        # ---- Level 8: onboarding (curated collection) ----
        # Each subkind defines its own gate inside build_context — slots
        # whose gates fail return None and are skipped entirely. Promoted
        # slots (project_overview / architecture_guide) are tagged onto the
        # level-6 pages and don't appear here.
        level8_coros: list[tuple[str, Any]] = []
        if getattr(self._config, "enable_onboarding", True):
            specs = _onboarding.iter_specs()
            if specs:
                if on_subphase is not None:
                    try:
                        on_subphase("onboarding", len(specs))
                    except Exception:
                        pass
                signals = _onboarding.OnboardingSignals(
                    repo_name=repo_name,
                    repo_structure=repo_structure,
                    parsed_files=tuple(parsed_files),
                    source_map=source_map,
                    graph_builder=graph_builder,
                    pagerank=pagerank,
                    betweenness=betweenness,
                    community=community,
                    sccs=tuple(sccs),
                    git_meta_map=git_meta_map,
                    dead_code_by_file=dead_code_by_file,
                    decisions_all=tuple(decisions_all),
                    external_systems=tuple(external_systems),
                    completed_page_summaries=dict(completed_page_summaries),
                )
                for spec in specs:
                    page_id = compute_page_id(
                        "onboarding", _onboarding.target_path(spec.slot)
                    )
                    if page_id in completed_ids:
                        continue
                    level8_coros.append(
                        (page_id, self.generate_onboarding_page(spec, signals))
                    )

        # Levels 6, 7, and 8 share no data dependencies with each other
        # — Level 6 needs only the graph + repo metadata; Level 7
        # documents standalone infra files; Level 8 consumes a frozen
        # ``OnboardingSignals`` snapshot. Run them in a single merged
        # batch instead of three sequential barriers.
        final_pages = await run_level(level6_coros + level7_coros + level8_coros, 8)
        # Tag promoted onboarding slots (repo_overview / architecture_diagram)
        # so the UI groups them into the Onboarding folder. No content change.
        self._tag_promoted_pages(final_pages)
        all_pages.extend(final_pages)

        # Post-generation: resolve backtick-quoted refs in every page's
        # markdown to other pages' ``page_id``s and stash the result in
        # ``metadata["wiki_links"]``. The reverse index lands in
        # ``metadata["backlinks"]``. Pure regex + dict lookup — no LLM
        # call, safe to run on every generation.
        try:
            from .interlinking import attach_wiki_links_and_backlinks

            attach_wiki_links_and_backlinks(all_pages, parsed_files)
        except Exception as exc:
            log.debug("interlinking.failed", error=str(exc))

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
        # RAG context: query vector store for related pages (B1).
        # Gated by two short-circuits so we don't burn an embedder
        # round-trip on every page when the result wouldn't help:
        #   1. ``enable_rag_context`` config flag (off → fully skip).
        #   2. ``rag_min_store_size`` — early pages run against an empty
        #      or near-empty store and the search returns nothing useful.
        if self._vector_store is not None and getattr(
            self._config, "enable_rag_context", True
        ):
            min_store_size = max(0, int(getattr(self._config, "rag_min_store_size", 10) or 0))
            store_ok = True
            if min_store_size > 0:
                try:
                    current_ids = await self._vector_store.list_page_ids()
                    store_ok = len(current_ids) >= min_store_size
                except Exception:
                    # If the store can't be sized cheaply, fall through to
                    # the search — it'll either succeed or hit the
                    # existing exception path.
                    store_ok = True
            if store_ok:
                query_terms = parsed.exports or [
                    s["name"] for s in ctx.symbols[:3] if s.get("visibility") == "public"
                ]
                if query_terms:
                    try:
                        results = await self._vector_store.search(
                            ", ".join(query_terms[:5]), limit=3
                        )
                        self_id = f"file_page:{parsed.file_info.path}"
                        ctx.rag_context = [
                            f"[{r.page_id}]\n{r.snippet}"
                            for r in results
                            if r.page_id != self_id
                        ]
                    except Exception as e:
                        log.debug(
                            "rag.search_failed", path=parsed.file_info.path, error=str(e)
                        )
        user_prompt = self._render("file_page.j2", ctx=ctx)
        response = await self._call_provider(
            "file_page", user_prompt, str(uuid.uuid4()), target_path=parsed.file_info.path
        )
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
        target_path: str | None = None,
    ) -> GeneratedResponse:
        """Call the provider with caching, optionally prefixing a language instruction."""
        # Persistent cross-run cache: if the page exists from a prior run, was
        # produced by the same model, and the prompt's source_hash matches,
        # reuse the stored content without an LLM call.
        if self._config.cache_enabled and target_path is not None:
            page_id = compute_page_id(page_type, target_path)
            prior = self._prior_pages.get(page_id)
            if prior is not None and prior.model_name == self._provider.model_name:
                current_hash = compute_source_hash(user_prompt)
                if prior.source_hash == current_hash:
                    self._reuse_count += 1
                    log.debug(
                        "page_cache.persistent_hit",
                        page_type=page_type,
                        target_path=target_path,
                    )
                    return GeneratedResponse(
                        content=prior.content,
                        input_tokens=0,
                        output_tokens=0,
                        cached_tokens=0,
                        usage={"reused_from_prior_run": True},
                    )

        key = self._compute_cache_key(page_type, user_prompt)
        if self._config.cache_enabled and key in self._cache:
            log.debug("Cache hit", page_type=page_type, key=key[:8])
            return self._cache[key]

        system_prompt = self._build_system_prompt(page_type)

        # The same system prompt is reused for every page of a given type, so
        # mark it as cacheable. Providers without server-side prompt caching
        # ignore the hint safely.
        cache_hints: tuple[CacheHint, ...] = (
            (CacheHint(segment="system"),) if self._config.cache_enabled else ()
        )

        response = await self._provider.generate(
            system_prompt,
            user_prompt,
            max_tokens=self._config.max_tokens,
            temperature=self._config.temperature,
            request_id=request_id,
            reasoning=self._config.reasoning,
            cache_hints=cache_hints,
        )

        if self._config.cache_enabled:
            self._cache[key] = response

        return response

    def _build_system_prompt(self, page_type: str) -> str:
        base_system = SYSTEM_PROMPTS[page_type]
        # Sanitize the configured language code: lower, strip, drop anything that isn't
        # alphanumeric or underscore. Prevents user-supplied config from injecting
        # newlines or extra instructions into the system prompt.
        raw = (self._language or "en").lower().strip()
        lang_code = "".join(ch for ch in raw if ch.isalnum() or ch == "_")
        if lang_code not in _LANGUAGE_NAMES:
            if lang_code != "en":
                log.warning("unknown_language_code", code=lang_code, fallback="en")
            lang_code = "en"
        if lang_code == "en":
            return base_system
        lang_name = _LANGUAGE_NAMES[lang_code]
        instruction = (
            f"Generate all documentation content in {lang_name}. "
            "Keep all code, file paths, and symbol names in their original form. "
            "Do not translate them.\n\n"
        )
        return instruction + base_system

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

    # Trivial-file gate: small files with almost no symbols (data classes,
    # marker classes, single-message wrappers like Messages/*.cs) produce
    # low-value pages. Entry points and graph hubs (high PageRank) bypass.
    if (
        getattr(config, "skip_trivial_files", True)
        and not is_entry
        and pr < pr_threshold * 2
        and len(parsed.symbols) <= 2
        and parsed.file_info.size_bytes < 1500
    ):
        return False

    # Waive the symbol-count requirement for graph-connected files that have
    # no original definitions of their own (e.g. state/config modules that
    # are imported by many files but mostly re-export or assemble values).
    if len(parsed.symbols) < config.file_page_min_symbols:
        return is_entry or pr >= pr_threshold

    return True


def _select_clone_representatives(
    code_files: list[ParsedFile],
    pagerank: dict[str, float],
    *,
    min_cluster_size: int = 3,
) -> set[str]:
    """Return paths of files to *drop* because they are near-clones.

    Groups files by (parent_directory, signature shape), where the shape is the
    sorted tuple of ``(symbol_kind, symbol_name)`` pairs from the parser. When
    a cluster has at least ``min_cluster_size`` members, the highest-PageRank
    member is kept and the rest are dropped. Entry points are never dropped.

    Language-agnostic: works for any language whose symbols carry a kind+name,
    which the parser guarantees.
    """
    from collections import defaultdict

    clusters: dict[tuple[str, tuple[tuple[str, str], ...]], list[ParsedFile]] = defaultdict(list)
    for p in code_files:
        if p.file_info.is_entry_point or not p.symbols:
            continue
        parent = str(Path(p.file_info.path).parent.as_posix())
        shape = tuple(sorted((str(s.kind), s.name) for s in p.symbols))
        clusters[(parent, shape)].append(p)

    drop: set[str] = set()
    for members in clusters.values():
        if len(members) < min_cluster_size:
            continue
        members.sort(key=lambda p: pagerank.get(p.file_info.path, 0.0), reverse=True)
        for loser in members[1:]:
            drop.add(loser.file_info.path)
    return drop


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
