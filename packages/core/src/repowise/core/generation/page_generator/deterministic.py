"""Template-rendered (no-LLM) variants of every page type.

The tier-2 file page proved the shape: take the context the LLM path already
assembles, render it through a Jinja template, and stamp the result as
template-generated. This module generalises that to the rest of the wiki so
``repowise init --index-only`` can produce a complete page set without a key.

Each deterministic template lives at ``templates/deterministic/<name>``,
mirroring the prompt template it replaces (``module_page.j2`` ->
``deterministic/module_page.j2``), so the pairing needs no registry.

What these pages are and are not: every sentence is derived from the parsed
AST, the import graph, git history or the knowledge graph, so they are factual
by construction and need no hallucination check. What they cannot do is
explain *why* the code is shaped the way it is. Page types whose whole value
is that synthesis (api_contract, infra_page) render an honest structural stub
rather than pretending. Every page carries ``metadata["deterministic"]`` so
the UI can offer to rewrite it with a model.
"""

from __future__ import annotations

from typing import Any

import structlog

from ..models import GENERATION_LEVELS, GeneratedPage, compute_page_id, compute_source_hash
from .helpers import _extract_summary, _now_iso

log = structlog.get_logger(__name__)

# Rendered under templates/deterministic/. Keeping the leaf name identical to
# the prompt template makes the pairing obvious when reading either side.
_DET_PREFIX = "deterministic"


class DeterministicRenderMixin:
    """Template-only ``_deterministic_*`` renderers, mixed into PageGenerator.

    Requires the host to provide ``_render``, ``_provider`` and ``_config``.
    """

    def _deterministic_page(
        self,
        *,
        page_type: str,
        target_path: str,
        title: str,
        template: str,
        doc_tier: int = 2,
        **render_kwargs: Any,
    ) -> GeneratedPage:
        """Render one template page and wrap it as a GeneratedPage.

        The mirror of ``_build_generated_page`` for the no-LLM path: same
        fields, zero tokens, ``provider_name="template"``.
        """
        content = self._render(f"{_DET_PREFIX}/{template}", style_prefix=False, **render_kwargs)
        now = _now_iso()
        # content_hash deliberately stays empty. The cross-run reuse gate keys
        # on model_name rather than provider_name, so stamping one here would
        # let a later LLM run serve this template content as if a model had
        # written it. Template pages are free to rebuild anyway.
        page = GeneratedPage(
            page_id=compute_page_id(page_type, target_path),
            page_type=page_type,
            title=title,
            content=content,
            summary=_extract_summary(content),
            source_hash=compute_source_hash(content),
            model_name=self._provider.model_name,
            provider_name="template",
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            generation_level=GENERATION_LEVELS[page_type],
            target_path=target_path,
            created_at=now,
            updated_at=now,
        )
        page.metadata["doc_tier"] = doc_tier
        page.metadata["deterministic"] = True
        return page

    # ------------------------------------------------------------------
    # Per-type renderers. One per LLM generate_* method it stands in for.
    # ------------------------------------------------------------------

    def _deterministic_symbol_spotlight(
        self, ctx: Any, target_path: str, title: str
    ) -> GeneratedPage:
        return self._deterministic_page(
            page_type="symbol_spotlight",
            target_path=target_path,
            title=title,
            template="symbol_spotlight.j2",
            ctx=ctx,
        )

    def _deterministic_module_page(
        self,
        ctx: Any,
        target_path: str,
        title: str,
        module_git_summary: dict | None,
    ) -> GeneratedPage:
        return self._deterministic_page(
            page_type="module_page",
            target_path=target_path,
            title=title,
            template="module_page.j2",
            ctx=ctx,
            module_git_summary=module_git_summary,
        )

    def _deterministic_scc_page(self, ctx: Any, scc_id: str, title: str) -> GeneratedPage:
        return self._deterministic_page(
            page_type="scc_page",
            target_path=scc_id,
            title=title,
            template="scc_page.j2",
            ctx=ctx,
            # Ranked by how many cross-edges each file carries: the highest
            # is the cheapest place to break the cycle. Computed here rather
            # than in Jinja because the template language makes grouping
            # painful and this is the one genuinely useful thing the page can
            # say that the LLM page says in prose.
            decouple_ranking=_rank_cycle_participants(ctx),
        )

    def _deterministic_repo_overview(
        self, ctx: Any, repo_name: str, title: str, repo_git_summary: dict | None
    ) -> GeneratedPage:
        return self._deterministic_page(
            page_type="repo_overview",
            target_path=repo_name,
            title=title,
            template="repo_overview.j2",
            ctx=ctx,
            repo_git_summary=repo_git_summary,
        )

    def _deterministic_architecture_diagram(
        self, ctx: Any, repo_name: str, title: str, overview_mermaid: str | None
    ) -> GeneratedPage:
        return self._deterministic_page(
            page_type="architecture_diagram",
            target_path=repo_name,
            title=title,
            template="architecture_diagram.j2",
            ctx=ctx,
            # Already structural on the LLM path too, where it overwrites
            # whatever diagram the model drew. Here it is simply the diagram.
            overview_mermaid=overview_mermaid or "",
        )

    def _deterministic_layer_page(self, ctx: Any, title: str) -> GeneratedPage:
        return self._deterministic_page(
            page_type="layer_page",
            target_path=ctx.layer_id,
            title=title,
            template="layer_page.j2",
            ctx=ctx,
        )

    def _deterministic_api_contract(self, ctx: Any, file_path: str, title: str) -> GeneratedPage:
        return self._deterministic_page(
            page_type="api_contract",
            target_path=file_path,
            title=title,
            template="api_contract.j2",
            ctx=ctx,
        )

    def _deterministic_infra_page(self, ctx: Any, file_path: str, title: str) -> GeneratedPage:
        return self._deterministic_page(
            page_type="infra_page",
            target_path=file_path,
            title=title,
            template="infra_page.j2",
            ctx=ctx,
        )

    def _deterministic_onboarding_page(
        self, spec: Any, ctx: Any, target_path: str
    ) -> GeneratedPage:
        page = self._deterministic_page(
            page_type="onboarding",
            target_path=target_path,
            title=spec.title,
            template=f"onboarding/{spec.template}",
            ctx=ctx,
            slot=spec.slot,
        )
        page.metadata["subkind"] = spec.slot
        page.metadata["onboarding_slot"] = spec.slot
        return page


def _rank_cycle_participants(ctx: Any) -> list[dict]:
    """Order a cycle's files by how many of its edges they carry.

    A file that both imports and is imported by the rest of the cycle is
    where the loop is tightest, so it is the first place to look when
    breaking it. Returns ``[{"path", "out", "in", "total"}]``, highest first.
    """
    counts: dict[str, dict[str, int]] = {f: {"out": 0, "in": 0} for f in ctx.files}
    for edge in ctx.cross_imports:
        src, dst = edge.get("from", ""), edge.get("to", "")
        if src in counts:
            counts[src]["out"] += 1
        if dst in counts:
            counts[dst]["in"] += 1
    ranked = [
        {"path": path, "out": c["out"], "in": c["in"], "total": c["out"] + c["in"]}
        for path, c in counts.items()
    ]
    ranked.sort(key=lambda r: (-r["total"], r["path"]))
    return ranked
