"""PageGenerator — renders prompts, calls the provider, wraps responses.

PageGenerator is the main orchestration layer. It:
    1. Calls ContextAssembler to build template context from ingestion data.
    2. Renders the Jinja2 user-prompt template.
    3. Calls the provider with the rendered prompt + system prompt constant.
    4. Wraps the response in a GeneratedPage.
    5. Manages concurrency (asyncio.Semaphore) and prompt caching (SHA256).

The level-by-level orchestration of ``generate_all`` lives in
``orchestrate.py``; the per-type ``generate_*`` methods live in ``pertype.py``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jinja2
import structlog

from repowise.core.ingestion.models import ParsedFile, RepoStructure
from repowise.core.providers.llm.base import BaseProvider, CacheHint, GeneratedResponse

from ..context_assembler import ContextAssembler, FilePageContext
from ..models import (
    GeneratedPage,
    GenerationConfig,
    compute_page_id,
    compute_source_hash,
)
from ..styles import ONBOARDING_PAGE_TYPE, resolve_style
from .helpers import _extract_summary, _now_iso
from .pertype import PerTypeGenerationMixin
from .prompts import SUPPORTED_LANGUAGES, SYSTEM_PROMPTS
from .structural import (
    StructuralRenderMixin,
    as_markdown,
    oneline,
    signature,
)

if TYPE_CHECKING:
    from pathlib import Path as _Path  # noqa: F401

log = structlog.get_logger(__name__)


def _attach_file_provenance(page: GeneratedPage, ctx: FilePageContext) -> None:
    """Surface KG layer + the inputs a file page was synthesised from.

    Reads only already-assembled context (no new work), so it is cheap and
    safe for both the LLM and the deterministic tier-2 path. The frontend
    renders ``layer_name`` as a zoom-out chip and ``sources`` as a "built
    from" provenance list.
    """
    from ...analysis.knowledge_graph import _slugify
    from ..layers import infer_layer

    if ctx.kg_layer_name:
        page.metadata["layer_name"] = ctx.kg_layer_name
        if ctx.kg_layer_role:
            page.metadata["layer_role"] = ctx.kg_layer_role
    else:
        # Guarantee every file page carries a layer so the Architecture tree
        # can group it. When the knowledge graph has no layer, fall back to
        # path-based inference.
        page.metadata["layer_name"] = infer_layer(ctx.file_path, getattr(ctx, "language", None))

    # ``layer_id`` is the join key every consumer groups on, so guarantee it
    # unconditionally. A curated layer_name without an id used to leave the
    # page unjoinable. The id is the stable slug; ``layer_name`` is display
    # text the LLM enrichment pass rewrites, and must never be a grouping key.
    # Derived with kg_curation's own slugify, so a fallback id is byte-identical
    # to the id that pass would mint for the same layer. A near-miss here points
    # a file page at a layer page that does not exist.
    page.metadata["layer_id"] = ctx.kg_layer_id or "layer:{}".format(
        _slugify(str(page.metadata["layer_name"]))
    )

    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    # Direct dependencies are the upstream files this doc draws on.
    for dep in ctx.dependencies[:10]:
        if dep and dep not in seen:
            seen.add(dep)
            sources.append({"path": dep, "kind": "dependency"})
    # Architectural decisions cite their own evidence file.
    for rec in ctx.decision_records[:5]:
        ev = rec.get("evidence_file") or rec.get("source")
        if ev and ev not in seen:
            seen.add(ev)
            sources.append({"path": ev, "kind": "decision"})
    if sources:
        page.metadata["sources"] = sources


@dataclass(frozen=True)
class PriorPage:
    """Snapshot of a previously-generated page used for cross-run reuse.

    Lives in :class:`PageGenerator` keyed by ``page_id``. When the freshly
    rendered prompt produces a matching ``source_hash`` under the same
    ``model_name``, the LLM call is skipped and ``content`` is reused.

    ``content_hash`` is the preferred reuse key when both sides have one: it
    stays stable across runs even when the rendered prompt drifts (RAG context
    is rebuilt and populated concurrently each run, so ``source_hash`` alone
    almost never matches on a reindex). Structural pages compute theirs in
    :meth:`StructuralRenderMixin._structural_content_hash`.
    """

    source_hash: str
    model_name: str
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    content_hash: str = ""


class PageGenerator(PerTypeGenerationMixin, StructuralRenderMixin):
    """Generate wiki pages by rendering prompts and calling an LLM provider.

    Args:
        provider:   Any BaseProvider implementation.
        assembler:  ContextAssembler instance.
        config:     GenerationConfig controlling budget, concurrency, caching.
        jinja_env:  Optional Jinja2 Environment (defaults to FileSystemLoader
                    pointing at the templates/ directory next to this package).
    """

    def __init__(
        self,
        provider: BaseProvider,
        assembler: ContextAssembler,
        config: GenerationConfig,
        jinja_env: jinja2.Environment | None = None,
        vector_store: Any | None = None,
        language: str | None = None,
        prior_pages: dict[str, PriorPage] | None = None,
        repo_path: Path | str | None = None,
    ) -> None:
        self._provider = provider
        self._assembler = assembler
        self._config = config
        self._vector_store = vector_store
        # Output language: explicit arg wins, else the config's, else English.
        self._language = language if language is not None else getattr(config, "language", "en")
        # Resolve the wiki style once. "comprehensive" (default) is inert, so this
        # is a no-op for repos that never opt in. ``repo_path`` lets a user-defined
        # ``.repowise/styles/<name>`` style resolve (Phase 5).
        self._style = resolve_style(getattr(config, "wiki_style", None), repo_path=repo_path)
        self._cache: dict[str, GeneratedResponse] = {}
        # Map of page_id → PriorPage from previous generation runs. When the
        # rendered prompt's source_hash matches the prior page's hash AND the
        # model is the same, the LLM call is skipped and the prior content is
        # reused. Wired by the orchestrator from the persisted wiki_pages
        # table.
        self._prior_pages: dict[str, PriorPage] = prior_pages or {}
        self._reuse_count: int = 0
        # Per-template structural fingerprints, lazily computed; every input
        # they fold is fixed for the generator's lifetime. Keyed by template
        # name because each structural page type folds its own template source.
        # See StructuralRenderMixin._structural_fingerprint.
        self._structural_fingerprints: dict[str, str] = {}

        if jinja_env is None:
            templates_dir = Path(__file__).parent.parent / "templates"
            # A custom style may ship its own templates/ dir (Layer 2). Resolve it
            # first via ChoiceLoader, falling back to the built-in templates for any
            # page type the style does not override.
            if self._style.template_dir is not None:
                loader: jinja2.BaseLoader = jinja2.ChoiceLoader(
                    [
                        jinja2.FileSystemLoader(str(self._style.template_dir)),
                        jinja2.FileSystemLoader(str(templates_dir)),
                    ]
                )
            else:
                loader = jinja2.FileSystemLoader(str(templates_dir))
            jinja_env = jinja2.Environment(
                loader=loader,
                undefined=jinja2.StrictUndefined,
                autoescape=False,
            )
        self._jinja_env = jinja_env
        # Registered on whatever env we ended up with (including one a caller
        # injected), since deterministic templates depend on it.
        self._jinja_env.filters.setdefault("oneline", oneline)
        self._jinja_env.filters.setdefault("as_markdown", as_markdown)
        self._jinja_env.filters.setdefault("signature", signature)

    # ------------------------------------------------------------------
    # generate_all — orchestration (delegates to orchestrate.py)
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
        on_page_ready: Callable[[GeneratedPage], None] | None = None,
        kg_modules: list[dict] | None = None,
        kg_data: dict | None = None,
        only_page_ids: set[str] | None = None,
    ) -> list[GeneratedPage]:
        """Generate all wiki pages for a repository.

        Runs generation in ordered levels. Each level's pages are generated
        concurrently (up to config.max_concurrency). Failures within a level
        are logged but do not abort the remaining levels.

        When ``only_page_ids`` is set, every level emits only the pages whose
        id is in the set. The whole repository is still parsed and its graph
        built, so a repo-wide page (overview, architecture, a module) that IS
        requested is generated from the complete view rather than a truncated
        one. This is the scoped-generation primitive behind ``repowise
        generate`` and the fix for callers that used to regenerate ten
        repo-wide pages as a side effect of asking for one file page.
        """
        from .orchestrate import run_generate_all

        return await run_generate_all(
            self,
            parsed_files=parsed_files,
            source_map=source_map,
            graph_builder=graph_builder,
            repo_structure=repo_structure,
            repo_name=repo_name,
            job_system=job_system,
            on_page_done=on_page_done,
            on_total_known=on_total_known,
            on_subphase=on_subphase,
            git_meta_map=git_meta_map,
            resume=resume,
            repo_path=repo_path,
            dead_code_report=dead_code_report,
            decision_report=decision_report,
            external_systems=external_systems,
            on_page_ready=on_page_ready,
            kg_modules=kg_modules,
            kg_data=kg_data,
            only_page_ids=only_page_ids,
        )

    # ------------------------------------------------------------------
    # File-page generation (LLM + deterministic tier-2)
    # ------------------------------------------------------------------

    async def _render_file_page(self, parsed: ParsedFile, ctx: FilePageContext) -> GeneratedPage:
        """Render a file page from structure. The only renderer for this type.

        Built straight from the assembled context via a Jinja template: no
        provider call, no tokens, and no hallucination check, because every
        statement on the page came from the parse, the import graph or git.
        The level runner embeds it for search like any other page.

        ``async`` with nothing awaited: the level runner gathers a list of
        page coroutines, and keeping this one shaped like the rest is cheaper
        than teaching that runner about two kinds of item.
        """
        page = self._structural_page(
            page_type="file_page",
            target_path=parsed.file_info.path,
            title=f"File: {parsed.file_info.path}",
            template="file_page.j2",
            subject_hash=parsed.content_hash or "",
            ctx=ctx,
        )
        # _render_page extracts the summary skipping a metadata preamble the
        # file template does not emit; keep this type's original extraction.
        page.summary = _extract_summary(page.content)
        _attach_file_provenance(page, ctx)
        return page

    # ------------------------------------------------------------------
    # Provider call + page assembly
    # ------------------------------------------------------------------

    async def _call_provider(
        self,
        page_type: str,
        user_prompt: str,
        request_id: str,
        target_path: str | None = None,
        source_salt: str = "",
    ) -> GeneratedResponse:
        """Call the provider with caching, optionally prefixing a language instruction.

        *source_salt* is folded into the source_hash used for cross-run reuse
        without changing the prompt sent to the model. Onboarding pages pass a
        generation-version salt so a builder/template upgrade forces a one-time
        regen even when the rendered prompt is byte-identical. Empty for every
        other page type, so their reuse hashes are unchanged.
        """
        # Persistent cross-run cache: if the page exists from a prior run, was
        # produced by the same model, and the prompt's source_hash matches,
        # reuse the stored content without an LLM call.
        if self._config.cache_enabled and target_path is not None:
            page_id = compute_page_id(page_type, target_path)
            prior = self._prior_pages.get(page_id)
            if prior is not None and prior.model_name == self._provider.model_name:
                if prior.source_hash == compute_source_hash(user_prompt + source_salt):
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
        # Wiki style: append the style's framing note. Constant per run (per page
        # type), so prefix caching still holds. Inert for the default style.
        base_system = base_system + self._style.system_prompt_suffix(
            is_onboarding=page_type == ONBOARDING_PAGE_TYPE
        )
        # Sanitize the configured language code: lower, strip, drop anything that isn't
        # alphanumeric or underscore. Prevents user-supplied config from injecting
        # newlines or extra instructions into the system prompt.
        raw = (self._language or "en").lower().strip()
        lang_code = "".join(ch for ch in raw if ch.isalnum() or ch == "_")
        if lang_code not in SUPPORTED_LANGUAGES:
            if lang_code != "en":
                log.warning("unknown_language_code", code=lang_code, fallback="en")
            lang_code = "en"
        if lang_code == "en":
            return base_system
        lang_name = SUPPORTED_LANGUAGES[lang_code]
        instruction = (
            f"Generate all documentation content in {lang_name}. "
            "Keep all code, file paths, and symbol names in their original form. "
            "Do not translate them.\n\n"
        )
        return instruction + base_system

    def _compute_cache_key(self, page_type: str, user_prompt: str) -> str:
        """Return SHA256(model + language + style + page_type + user_prompt) as cache key.

        The style fingerprint is already embedded in ``user_prompt`` for active
        styles, but include it explicitly so the in-memory cache never collides
        across styles even if a future change moves the directive out of the prompt
        body. Empty for the default style → key is unchanged from before.
        """
        raw = (
            f"{self._provider.model_name}:{self._language}:"
            f"{self._style.fingerprint}:{page_type}:{user_prompt}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def _build_generated_page(
        self,
        page_type: str,
        target_path: str,
        title: str,
        response: GeneratedResponse,
        source_hash: str,
        level: int,
        content_hash: str = "",
    ) -> GeneratedPage:
        """Wrap a GeneratedResponse in a GeneratedPage."""
        now = _now_iso()
        page = GeneratedPage(
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
            content_hash=content_hash,
        )
        # Record the effective style as page provenance (D10). Only for active
        # styles, so default ("comprehensive") pages keep byte-identical metadata.
        if self._style.is_active:
            page.metadata["style"] = self._style.name
        # Content came back verbatim from the prior run's persisted page:
        # downstream (embedding) uses this to skip work whose output already
        # exists byte-identically.
        if response.usage.get("reused_from_prior_run"):
            page.metadata["reused_from_prior_run"] = True
        return page

    def _render(self, template_name: str, *, style_prefix: bool = True, **kwargs: Any) -> str:
        """Render a Jinja2 template with the given kwargs.

        For LLM *prompts* (the default), the active wiki style's directive is
        prepended so the model adjusts its voice and — critically — the directive
        becomes part of the rendered text that ``source_hash`` is computed over, so
        a style change invalidates the cache and regenerates the page on update.

        ``style_prefix=False`` is for deterministic templates whose render output is
        the page *content* itself (tier-2 file pages), not a prompt — those must not
        carry a style directive.
        """
        template = self._jinja_env.get_template(template_name)
        body = template.render(**kwargs)
        if not style_prefix:
            return body
        is_onboarding = template_name.startswith("onboarding/")
        prefix = self._style.user_prompt_prefix(is_onboarding=is_onboarding)
        return prefix + body if prefix else body
