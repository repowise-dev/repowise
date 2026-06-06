"""Per-page-type generation methods, mixed into :class:`PageGenerator`.

Each method assembles a context, renders its Jinja template into a user
prompt, calls the provider, and wraps the response in a ``GeneratedPage``.
They are grouped here (rather than inline on the generator) purely to keep
each module under the project's 400-line ceiling.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from repowise.core.ingestion.models import ParsedFile, RepoStructure

from .. import onboarding as _onboarding
from ..context_assembler import FilePageContext, LayerPageContext
from ..models import GENERATION_LEVELS, GeneratedPage, compute_source_hash

log = structlog.get_logger(__name__)


class PerTypeGenerationMixin:
    """Per-type ``generate_*`` methods. Requires the host to provide
    ``_assembler``, ``_render``, ``_call_provider`` and
    ``_build_generated_page`` (all supplied by :class:`PageGenerator`).
    """

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
        spec: _onboarding.SubkindSpec,
        signals: _onboarding.OnboardingSignals,
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

    async def generate_layer_page(
        self,
        ctx: LayerPageContext,
    ) -> GeneratedPage:
        user_prompt = self._render("layer_page.j2", ctx=ctx)
        # target_path = the layer's STABLE slug id, so the page key survives
        # the post-generation LLM rename of ``layer_name``. The title still
        # uses the (heuristic) display name.
        target = ctx.layer_id
        response = await self._call_provider(
            "layer_page", user_prompt, str(uuid.uuid4()), target_path=target
        )
        return self._build_generated_page(
            "layer_page",
            target,
            f"Layer: {ctx.layer_name}",
            response,
            compute_source_hash(user_prompt),
            GENERATION_LEVELS["layer_page"],
        )

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
