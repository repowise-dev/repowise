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
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jinja2
import structlog

from repowise.core.ingestion.models import ParsedFile, RepoStructure
from repowise.core.providers.llm.base import BaseProvider, CacheHint, GeneratedResponse

from ..context_assembler import ContextAssembler, FilePageContext
from ..models import (
    GENERATION_LEVELS,
    GeneratedPage,
    GenerationConfig,
    compute_page_id,
    compute_source_hash,
)
from ..styles import ONBOARDING_PAGE_TYPE, resolve_style
from .decision_harvest import (
    HARVEST_DIRECTIVE,
    HARVESTABLE_PAGE_TYPES,
    harvest_decisions,
)
from .deterministic import DeterministicRenderMixin, oneline
from .helpers import _extract_summary, _now_iso
from .pertype import PerTypeGenerationMixin
from .prompts import SUPPORTED_LANGUAGES, SYSTEM_PROMPTS
from .validation import _validate_symbol_references, validate_generated_response

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
    if ctx.kg_layer_name:
        page.metadata["layer_name"] = ctx.kg_layer_name
        # Stable slug id of the layer page this file links to. The layer page
        # is keyed by slug (``layer:<slug>``) so the join survives the LLM
        # layer-name enrichment that mutates ``layer_name`` after generation.
        if ctx.kg_layer_id:
            page.metadata["layer_id"] = ctx.kg_layer_id
        if ctx.kg_layer_role:
            page.metadata["layer_role"] = ctx.kg_layer_role
    else:
        # Guarantee every file page carries a layer so the Architecture tree
        # can group it. When the knowledge graph has no layer, fall back to
        # path-based inference.
        from ...analysis.knowledge_graph import _slugify
        from ..layers import infer_layer

        inferred = infer_layer(ctx.file_path, getattr(ctx, "language", None))
        page.metadata["layer_name"] = inferred
        page.metadata["layer_id"] = f"layer:{_slugify(inferred)}"

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

    ``content_hash`` (see :meth:`PageGenerator._reuse_content_hash`) is the
    preferred reuse key when both sides have one: it stays stable across
    runs even when the rendered prompt drifts (RAG context is rebuilt and
    populated concurrently each run, so ``source_hash`` alone almost never
    matches on a reindex).
    """

    source_hash: str
    model_name: str
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    content_hash: str = ""


class PageGenerator(PerTypeGenerationMixin, DeterministicRenderMixin):
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
        # One-line summary of the FAQ-weighted budget tilt for this run, set by
        # the orchestrator when session demand was found (else None). The CLI
        # surfaces it after generation so the usage-weighting is visible.
        self.faq_demand_summary: str | None = None
        # Lazily computed by _generation_fingerprint(); every input it folds
        # is fixed for the generator's lifetime.
        self._gen_fingerprint: str | None = None

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

    async def _generate_file_page_from_ctx(
        self,
        parsed: ParsedFile,
        ctx: FilePageContext,
        rag_prefetched: bool = False,
    ) -> GeneratedPage:
        """Generate a file_page from a pre-assembled context (avoids double-assembly).

        *rag_prefetched* is set by the level-2 builder when RAG context was
        already resolved for the whole level in one batched search (see
        ``levels._prefetch_rag_context``) — the per-page search below would
        re-fetch identical results inside the LLM semaphore, so it is skipped.
        """
        # RAG context: query vector store for related pages (B1).
        # Gated by two short-circuits so we don't burn an embedder
        # round-trip on every page when the result wouldn't help:
        #   1. ``enable_rag_context`` config flag (off → fully skip).
        #   2. ``rag_min_store_size`` — early pages run against an empty
        #      or near-empty store and the search returns nothing useful.
        if (
            not rag_prefetched
            and self._vector_store is not None
            and getattr(self._config, "enable_rag_context", True)
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
                            f"[{r.page_id}]\n{r.snippet}" for r in results if r.page_id != self_id
                        ]
                    except Exception as e:
                        log.debug("rag.search_failed", path=parsed.file_info.path, error=str(e))
        user_prompt = self._render("file_page.j2", ctx=ctx)
        content_hash = self._reuse_content_hash(parsed)
        response = await self._call_provider(
            "file_page",
            user_prompt,
            str(uuid.uuid4()),
            target_path=parsed.file_info.path,
            content_hash=content_hash,
        )
        harvested = self._strip_harvested_decisions(response, ctx, parsed.file_info.path)
        # Cross-check LLM output against actual symbols
        hal_warnings = _validate_symbol_references(response.content, parsed)
        repair_outcome: str | None = None
        threshold = self._config.repair_warning_threshold
        if (
            threshold > 0
            and len(hal_warnings) >= threshold
            and not response.usage.get("reused_from_prior_run")
        ):
            response, harvested, hal_warnings, repair_outcome = await self._repair_file_page(
                parsed, ctx, user_prompt, response, harvested, hal_warnings
            )
        page = self._build_generated_page(
            "file_page",
            parsed.file_info.path,
            f"File: {parsed.file_info.path}",
            response,
            compute_source_hash(user_prompt),
            GENERATION_LEVELS["file_page"],
            content_hash=content_hash,
        )
        if harvested:
            page.metadata["harvested_decisions"] = harvested
        if hal_warnings:
            log.warning(
                "hallucination_check",
                path=parsed.file_info.path,
                count=len(hal_warnings),
                refs=hal_warnings[:5],
            )
            page.metadata["hallucination_warnings"] = hal_warnings
        if repair_outcome:
            page.metadata["self_repair"] = repair_outcome
        _attach_file_provenance(page, ctx)
        return page

    def _strip_harvested_decisions(
        self,
        response: GeneratedResponse,
        ctx: FilePageContext,
        evidence_file: str,
    ) -> list[dict]:
        """Phase-2 harvest: pull any trailing decision block out of the page
        before it is wrapped + stored. The gate verifies each quote against
        ``file_source_snippet`` — exactly what the model was shown — so a
        quote it can't have seen is dropped. Returned for page.metadata so the
        persistence layer can fold it into the evidence pipeline.
        """
        if not self._config.harvest_decisions:
            return []
        clean_content, harvested = harvest_decisions(
            response.content,
            source_text=ctx.file_source_snippet or "",
            evidence_file=evidence_file,
        )
        response.content = clean_content
        return harvested

    async def _repair_file_page(
        self,
        parsed: ParsedFile,
        ctx: FilePageContext,
        user_prompt: str,
        response: GeneratedResponse,
        harvested: list[dict],
        hal_warnings: list[str],
    ) -> tuple[GeneratedResponse, list[dict], list[str], str]:
        """Bounded self-repair for hallucinated symbol references.

        Re-calls the provider once with the invalid refs named in a corrective
        note, re-validates, and keeps whichever draft validates cleaner. Both
        calls' tokens are folded into the kept response so page-level token
        accounting (and cost) reflects the real spend. One retry per page,
        never recursive; the caller has already excluded reused prior pages.
        """
        path = parsed.file_info.path
        correction = (
            "\n\nIMPORTANT CORRECTION: a previous draft of this page referenced "
            "identifiers that do not exist in this file: "
            + ", ".join(f"`{r}`" for r in sorted(hal_warnings)[:15])
            + ". Do not mention these names. Only reference symbols, imports, "
            "exports, and files that appear in the context above."
        )
        # No target_path/content_hash here: the retry must reach the provider,
        # since the cross-run reuse gate would hand back the draft that failed.
        retry = await self._call_provider("file_page", user_prompt + correction, str(uuid.uuid4()))
        retry_harvested = self._strip_harvested_decisions(retry, ctx, path)
        retry_warnings = _validate_symbol_references(retry.content, parsed)
        improved = len(retry_warnings) < len(hal_warnings)
        log.info(
            "hallucination_repair",
            path=path,
            warnings_before=len(hal_warnings),
            warnings_after=len(retry_warnings),
            kept="retry" if improved else "original",
        )
        if improved:
            kept, kept_harvested, kept_warnings = retry, retry_harvested, retry_warnings
        else:
            kept, kept_harvested, kept_warnings = response, harvested, hal_warnings
        # replace() rather than mutating: either draft may also live in the
        # in-memory prompt cache, which must keep its per-call token counts.
        kept = replace(
            kept,
            input_tokens=response.input_tokens + retry.input_tokens,
            output_tokens=response.output_tokens + retry.output_tokens,
            cached_tokens=response.cached_tokens + retry.cached_tokens,
        )
        return kept, kept_harvested, kept_warnings, "improved" if improved else "kept_original"

    async def _generate_file_page_tier2(
        self,
        parsed: ParsedFile,
        ctx: FilePageContext,
        *,
        tail: bool = False,
    ) -> GeneratedPage:
        """Render a deterministic (no-LLM) tier-2 file page.

        Used for the long tail of selected files on large repos, and (with
        ``tail=True``) for the Phase G coverage tail: code files the budget
        dropped entirely, so the whole codebase is retrievable by concept
        search. The page is built straight from the assembled context via a
        Jinja template, marked as template-generated, and carries zero token
        cost. It is embedded for search by the level runner like any other
        page. No provider call and no hallucination check (the content is
        factual by construction).

        ``tail=True`` stamps ``doc_tier=3`` (vs 2 for the in-budget tail) so
        serving/ranking and the UI can tell budget-tail coverage pages apart
        and rank them below LLM pages; ``metadata["deterministic"]`` marks both.
        """
        content = self._render("file_page_tier2.j2", style_prefix=False, ctx=ctx)
        now = _now_iso()
        # content_hash deliberately stays empty: the cross-run reuse gate keys
        # on model_name (not provider_name), so stamping it here would let a
        # later tier-1 (LLM) run reuse this deterministic template content as
        # if the LLM had written it. Tier-2 pages are free to rebuild anyway.
        page = GeneratedPage(
            page_id=compute_page_id("file_page", parsed.file_info.path),
            page_type="file_page",
            title=f"File: {parsed.file_info.path}",
            content=content,
            summary=_extract_summary(content),
            source_hash=compute_source_hash(content),
            model_name=self._provider.model_name,
            provider_name="template",
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            generation_level=GENERATION_LEVELS["file_page"],
            target_path=parsed.file_info.path,
            created_at=now,
            updated_at=now,
        )
        page.metadata["doc_tier"] = 3 if tail else 2
        page.metadata["deterministic"] = True
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
        content_hash: str = "",
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
        # produced by the same model, and either the documented file's bytes
        # (content_hash) or the prompt's source_hash matches, reuse the stored
        # content without an LLM call. content_hash is checked first: the
        # rendered prompt embeds RAG context rebuilt fresh each run, so the
        # prompt hash alone misses on unchanged files.
        if self._config.cache_enabled and target_path is not None:
            page_id = compute_page_id(page_type, target_path)
            prior = self._prior_pages.get(page_id)
            if prior is not None and prior.model_name == self._provider.model_name:
                reuse = bool(content_hash) and prior.content_hash == content_hash
                if not reuse:
                    reuse = prior.source_hash == compute_source_hash(user_prompt + source_salt)
                if reuse:
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
        validate_generated_response(response)

        if self._config.cache_enabled:
            self._cache[key] = response

        return response

    def _build_system_prompt(self, page_type: str) -> str:
        base_system = SYSTEM_PROMPTS[page_type]
        # Phase-2 decision harvest: extend the (otherwise constant) system
        # prompt with the harvest directive on the page types we harvest from.
        # The directive is constant per run, so prefix caching still holds.
        if self._config.harvest_decisions and page_type in HARVESTABLE_PAGE_TYPES:
            base_system = base_system + HARVEST_DIRECTIVE
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

    def _generation_fingerprint(self) -> str:
        """Hash of every fixed input (besides the file itself) that shapes a
        file page's content.

        The prompt-hash reuse path caught changes to any of these for free —
        they all end up in the rendered prompt or system prompt. The
        content-hash path deliberately ignores the prompt, so it must fold
        them explicitly or a template upgrade / language switch / style
        switch / harvest toggle would silently keep serving old pages for
        unchanged files:

        * the file_page prompt template source (a repowise upgrade that
          improves the template must regenerate),
        * the file_page system prompt constant (same reason),
        * the output language,
        * the wiki-style fingerprint (empty for the default style),
        * the decision-harvest flag (its directive changes what pages carry).

        Graph/RAG/neighbor context is deliberately NOT folded — drifting on
        every run is exactly what made prompt-hash reuse never fire.
        """
        if self._gen_fingerprint is None:
            try:
                template_src = self._jinja_env.loader.get_source(  # type: ignore[union-attr]
                    self._jinja_env, "file_page.j2"
                )[0]
            except Exception:
                template_src = ""
            raw = "\x00".join(
                [
                    template_src,
                    SYSTEM_PROMPTS.get("file_page", ""),
                    self._language or "en",
                    self._style.fingerprint,
                    "harvest" if self._config.harvest_decisions else "",
                ]
            )
            self._gen_fingerprint = hashlib.sha256(raw.encode()).hexdigest()
        return self._gen_fingerprint

    def _reuse_content_hash(self, parsed: ParsedFile) -> str:
        """Return the cross-run reuse key for a page built from *parsed*:
        SHA256 of the file's raw-bytes hash folded with the generation
        fingerprint. Stable across runs while the file and the generation
        settings are unchanged; changes when either does. Empty when the
        parse didn't produce a content hash (never matches — always
        regenerates)."""
        if not parsed.content_hash:
            return ""
        raw = f"{parsed.content_hash}:{self._generation_fingerprint()}"
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
