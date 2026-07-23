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

import re
from typing import Any

import structlog

from ..models import GENERATION_LEVELS, GeneratedPage, compute_page_id, compute_source_hash
from .helpers import _extract_summary, _now_iso

log = structlog.get_logger(__name__)

# Rendered under templates/deterministic/. Keeping the leaf name identical to
# the prompt template makes the pairing obvious when reading either side.
_DET_PREFIX = "deterministic"

# Cap for free text folded into a list item or table cell. Long enough for a
# real summary sentence, short enough that a bullet stays a bullet.
_ONELINE_LIMIT = 200


def oneline(value: object, limit: int = _ONELINE_LIMIT) -> str:
    """Flatten free text so it can sit inside a markdown list item or cell.

    Deterministic templates interpolate text the pipeline produced elsewhere:
    docstrings, page summaries, decision rationales. That text is routinely
    multi-paragraph, and a raw newline inside a bullet ends the list, dumping
    the remainder as body text and restarting the numbering after it. The LLM
    templates never had this problem because their output was a prompt, not
    page content.
    """
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


# reStructuredText roles (:meth:`x`, :class:`~pkg.X`) and the double-backtick
# literal. Our docstrings are predominantly Sphinx flavoured, and markdown
# renders none of it: ``:meth:`foo``` shows the role name as body text and the
# double backticks come out as a stray empty code span.
_REST_ROLE_RE = re.compile(r":[a-z:]+:`~?([^`]+)`")
_REST_DIRECTIVE_RE = re.compile(r"^\s*\.\.\s+[a-z-]+::.*$", re.MULTILINE)
_DOUBLE_TICK_RE = re.compile(r"``([^`]+)``")


def as_markdown(value: object) -> str:
    """Convert a source docstring into markdown that renders as intended.

    Docstrings reach a deterministic page verbatim, so whatever dialect the
    author used lands in the rendered wiki. Sphinx roles and directives are by
    far the most common here and are also the ones markdown mangles worst, so
    those are converted; everything else is left alone rather than guessed at.
    """
    text = str(value or "")
    if not text.strip():
        return ""
    text = _REST_DIRECTIVE_RE.sub("", text)
    # ``:meth:`Store.get``` -> ``Store.get``, keeping the reference visible as
    # code rather than dropping it.
    text = _REST_ROLE_RE.sub(r"`\1`", text)
    text = _DOUBLE_TICK_RE.sub(r"`\1`", text)
    return dedent_body(text).strip()


def dedent_body(text: str) -> str:
    """Strip the common indent from every line after the first.

    A docstring's first line starts at the quote, later lines carry the source
    indentation. Left in, four or more leading spaces make markdown treat the
    body as a code block.
    """
    lines = text.splitlines()
    if len(lines) < 2:
        return text
    rest = [ln for ln in lines[1:] if ln.strip()]
    if not rest:
        return lines[0]
    indent = min(len(ln) - len(ln.lstrip()) for ln in rest)
    return "\n".join([lines[0]] + [ln[indent:] if ln.strip() else "" for ln in lines[1:]])


def signature(value: object, limit: int = 120) -> str:
    """Render a symbol signature for a table cell without cutting mid-token.

    Signatures are captured across source lines, so collapsing whitespace is
    needed before anything else or the cell fills with runs of indentation.
    When one is still too long we cut back to the last argument boundary, so
    the reader sees a whole parameter list prefix rather than half an
    identifier.
    """
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind(", "), head.rfind("("))
    if cut > limit // 3:
        head = head[: cut + 1]
    return head.rstrip().rstrip(",") + " …"


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
            summary=_extract_summary(content, skip_metadata=True),
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
