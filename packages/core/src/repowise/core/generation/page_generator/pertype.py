"""Per-page-type generation methods, mixed into :class:`PageGenerator`.

Each method assembles a context, renders its Jinja template into a user
prompt, calls the provider, and wraps the response in a ``GeneratedPage``.
They are grouped here (rather than inline on the generator) purely to keep
each module under the project's 400-line ceiling.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import structlog

from repowise.core.ingestion.models import ParsedFile, RepoStructure

from .. import onboarding as _onboarding
from ..architecture_mermaid import embed_mermaid
from ..context.assembler import build_concept_index
from ..context_assembler import FilePageContext
from ..models import (
    GENERATION_LEVELS,
    STUB_FALLBACK_ERROR,
    STUB_PAGE_CONFIDENCE,
    GeneratedPage,
    compute_source_hash,
)
from ..overview_tables import (
    Capability,
    build_capability_table,
    build_package_table,
    embed_capability_table,
    embed_package_table,
)
from ..structural_labels import structural_page_title

log = structlog.get_logger(__name__)


def _stub_fallback(page: GeneratedPage, page_type: str, exc: Exception) -> GeneratedPage:
    """Mark *page* as the stub standing in for a model page the provider lost.

    Dropping the page instead is what made a provider outage unrecoverable
    (issue #1089): scope resolution runs over persisted page records, so a page
    no run ever wrote is invisible to ``generate`` and to ``update`` alike, and
    nothing can ask for it again. The stub is already the shape these four page
    types take before a model writes them, so substituting it costs one render
    and leaves a row that ``repowise generate`` refills like any other stub.

    The error is stamped rather than only logged because the level runner reads
    it back: this page still has to count as a failure in the job checkpoint,
    and must not reach the vector store that ``--resume`` treats as its record
    of what is already done.

    Lowering the confidence here rather than in ``_stub_page`` is the whole of
    the distinction. Both paths render the identical template, so the bytes
    cannot tell them apart. What differs is that this one was *supposed* to be
    prose. A keyless run's stub is a finished deterministic page and keeps
    :data:`TEMPLATE_PAGE_CONFIDENCE`; this one is a stand-in for something the
    run intended and could not produce, and that is the state worth flagging to
    a reader. Stamping both meant a keyless wiki flagged every model-written
    page it had, which is all of them.
    """
    page.metadata[STUB_FALLBACK_ERROR] = str(exc)[:500]
    page.confidence = STUB_PAGE_CONFIDENCE
    log.warning(
        "page_generation.stub_fallback",
        page_type=page_type,
        target_path=page.target_path,
        error=str(exc),
    )
    return page


def _with_architecture_map(page: GeneratedPage, overview_mermaid: str | None) -> GeneratedPage:
    """Embed the KG-derived architecture map into an already-built page.

    The overview is where the map lives, so the stub paths carry it too — a
    provider outage should cost the prose around the diagram, not the diagram.
    Embedding is idempotent, so calling this on a page that already has one is
    safe.
    """
    if not overview_mermaid:
        return page
    page.content = embed_mermaid(page.content, overview_mermaid, heading="## Architecture map")
    return page


def _with_package_table(page: GeneratedPage, package_stats: list[dict]) -> GeneratedPage:
    """Embed the package table into an already-built page.

    Same reasoning as the architecture map, and the same three paths: the model
    page, the deterministic page and the provider-outage fallback all carry it,
    because which packages exist is a fact the run already holds. Writing it
    through the model instead meant it was resampled on every render — two
    calls with the same prompt disagreed on the row count.

    Embedding is idempotent and replaces the model's own ``## Packages``
    section, so a reused or cached page picks up the current counts rather than
    accumulating a second list.
    """
    if not package_stats:
        return page
    page.content = embed_package_table(page.content, build_package_table(package_stats))
    return page


def _with_capability_table(
    page: GeneratedPage, capabilities: Sequence[Capability]
) -> GeneratedPage:
    """Embed the capability table into an already-built page.

    Same three paths and the same reasoning as the package table. What the
    repository calls its own capabilities is read from its documents and
    corroborated against its module pages, both of which the run already
    holds — so the model page, the ``--no-prose`` page and the outage fallback
    carry identical bytes, and a reader diffing two updates sees a code change
    rather than a re-roll.
    """
    if not capabilities:
        return page
    page.content = embed_capability_table(page.content, build_capability_table(capabilities))
    return page


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
        """Render a file page from structure. Never calls a model.

        A file page states what a parser already knows exactly: symbols,
        signatures, imports, dependents, git history. A model adds nothing to
        that and introduces staleness, so there is one renderer and no key is
        needed for it. ``async`` is kept because the level runner awaits every
        page coroutine uniformly.
        """
        ctx = self._assembler.assemble_file_page(
            parsed, graph, pagerank, betweenness, community, source_bytes
        )
        return await self._render_file_page(parsed, ctx)

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
        target = f"{parsed.file_info.path}::{symbol.name}"
        # The subject is the defining file's bytes — the same subject the file
        # page uses. A spotlight renders a symbol out of that file, so when the
        # bytes move the spotlight has to be redone anyway, and there is no
        # finer-grained per-symbol hash stored to compare against. Without a
        # subject the page stores no render key at all and the staleness sweep
        # can never see that an improved template has not reached it.
        return self._structural_symbol_spotlight(
            ctx,
            target,
            structural_page_title(self._language, "symbol_spotlight", symbol.qualified_name),
            subject_hash=parsed.content_hash or "",
        )

    async def generate_module_page(
        self,
        title: str,
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
        structural_key: str = "",
        members: list[str] | None = None,
        section: str = "",
        order: int = 0,
        scope: str = "",
        is_rollup: bool = False,
        child_pages: list[dict] | None = None,
        owns_files: bool = True,
    ) -> GeneratedPage:
        ctx = self._assembler.assemble_module_page(
            title,
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
            scope=scope,
            is_rollup=is_rollup,
            child_pages=child_pages,
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
        # The page id is built from this, so it has to be a path, and it has
        # to be the same path the grouper chose: it computes one shared
        # directory per group and guarantees the result is unique across
        # groups. Picking a different one here (the first directory, say)
        # would mint an id the rest of the run does not agree with, so a
        # caller that supplies no target gets the group's own anchor or, for
        # a group with no directory at all, the root name the grouper uses.
        page_target = target_path or (min(ctx.directories) if ctx.directories else "root")
        # The files this page covers. Its own identity and its place in the
        # tree both derive from them: the page groups several directories, so
        # its target_path is one directory the group touches rather than a
        # container for all of it, and the member list is the only thing that
        # says where the page belongs.
        #
        # Taken from the group rather than from ``file_contexts``, and the
        # difference is load-bearing. A file context only exists for a file the
        # run built one for, so deriving members here would silently narrow the
        # page to that subset while ``structural_key`` still hashed the whole
        # group. Placement resolves file ownership from this list, so the
        # dropped files would be parented somewhere else entirely, and the two
        # records of what the page covers would disagree.
        # A chapter with no group of its own owns no files: everything beneath
        # it already belongs to the leaves below, so claiming them here would
        # parent those files twice and scramble the tree. It keeps an empty
        # member list, which is exactly what ``assign_page_tree`` reads as
        # "owns nothing". A chapter that *is* also a leaf directory owns its own
        # loose files and passes ``owns_files``, which is why this is keyed on
        # ownership rather than on ``is_rollup``.
        if owns_files:
            covered = sorted(members) if members else sorted(fc.file_path for fc in file_contexts)
        else:
            covered = []

        def _stamp_concept(page: GeneratedPage) -> GeneratedPage:
            """Record where the namer put this page in the reading order.

            Only stamped when a namer ran, so an absent key means "nobody
            decided", which is what the tree needs to tell apart from "first".
            Display only: the tree reads ``concept_order`` to order siblings,
            and neither field takes part in page identity, so re-sectioning a
            wiki renumbers it and mints no new pages.
            """
            page.metadata["file_paths"] = covered
            if is_rollup:
                # What makes this page a chapter rather than a leaf, recorded
                # so the tree can nest its children under it. Derivable from
                # the page set — a directory with two or more module pages
                # immediately below it — but deriving it there would be a
                # second place computing the rule that decided the page, and
                # the two would have to agree forever. Absent on a page written
                # before this shipped, which reads as "leaf" and leaves those
                # wikis flat rather than guessing.
                page.metadata["is_chapter"] = True
            if section:
                page.metadata["concept_section"] = section
                page.metadata["concept_order"] = order
            # Set by the producer, preserved by ``_stamp_structural_keys``. The
            # grouper hashed exactly this member list when it decided what the
            # group was, so recomputing it downstream would give two places
            # that must agree about page identity — the arrangement D2 rules out.
            page.structural_key = structural_key or page.structural_key
            return page

        def _with_concept_index(page: GeneratedPage) -> GeneratedPage:
            """Append the module's own identifiers to whatever wrote the page.

            After generation rather than inside the prompt, and that placement
            is the point. ``module_page.j2`` is a prompt: a table put in it is
            material the model may reformat, abbreviate or drop, and a page
            whose identifiers came out of a provider is exactly as trustworthy
            as the prose around them. Appended here, every name and path is the
            symbol index's and no response can change it.

            After ``_build_generated_page`` too, so the page summary is still
            drawn from the model's opening rather than from a table row.
            """
            rows, omitted = build_concept_index(file_contexts)
            if not rows:
                return page
            table = self._render(
                "_concept_index_table.j2", style_prefix=False, rows=rows, omitted=omitted
            )
            page.content = f"{(page.content or '').rstrip()}\n\n{table.strip()}\n"
            return page

        if self._config.deterministic:
            page = self._stub_module_page(ctx, page_target, title, module_git_summary)
            return _stamp_concept(_with_concept_index(page))
        user_prompt = self._render("module_page.j2", ctx=ctx, module_git_summary=module_git_summary)
        try:
            response = await self._call_provider(
                "module_page", user_prompt, str(uuid.uuid4()), target_path=page_target
            )
        except Exception as exc:
            stub = self._stub_module_page(ctx, page_target, title, module_git_summary)
            return _stamp_concept(_with_concept_index(_stub_fallback(stub, "module_page", exc)))
        page = self._build_generated_page(
            "module_page",
            page_target,
            title,
            response,
            compute_source_hash(user_prompt),
            GENERATION_LEVELS["module_page"],
        )
        return _stamp_concept(_with_concept_index(page))

    async def generate_scc_page(
        self,
        scc_id: str,
        scc_files: list[str],
        file_contexts: list[FilePageContext],
        title: str | None = None,
    ) -> GeneratedPage:
        from ..concept_tree.naming import scc_where

        ctx = self._assembler.assemble_scc_page(scc_id, scc_files, file_contexts)
        members = sorted(scc_files)
        # Titled by where the cycle is, not by the hash of its member list. The
        # id keeps the hash, so nothing is redirected and no link breaks; only
        # the words a reader and a search see change.
        #
        # The caller names the whole set at once, because uniqueness is a
        # property of the set. This fallback is for a caller that has one
        # cycle and no set — the name is still better than the hash, and the
        # collision it cannot see is one two identical names would have had.
        if not title:
            where = scc_where(members)
            title = structural_page_title(self._language, "scc_page", where or scc_id)
        page = self._structural_scc_page(ctx, scc_id, title)
        page.metadata["file_paths"] = members
        return page

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
        overview_mermaid: str | None = None,
        source_map: dict[str, bytes] | None = None,
        parsed_files: list[ParsedFile] | None = None,
        capabilities: Sequence[Capability] = (),
        prose_digest: str = "",
    ) -> GeneratedPage:
        ctx = self._assembler.assemble_repo_overview(
            repo_structure,
            pagerank,
            sccs,
            community,
            graph_builder=graph_builder,
            repo_name=repo_name,
            external_systems=external_systems,
            decision_records=decision_records,
            parsed_files=parsed_files,
            prose_digest=prose_digest,
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
        if self._config.deterministic:
            stub = self._stub_repo_overview(
                ctx, repo_name, f"Repository Overview: {repo_name}", repo_git_summary
            )
            page = _with_architecture_map(
                _with_package_table(_with_capability_table(stub, capabilities), ctx.package_stats),
                overview_mermaid,
            )
            selection = self._disabled_source_evidence("repo_overview", "deterministic_generation")
            return self._attach_source_evidence(page, "repo_overview", selection)
        user_prompt = self._render("repo_overview.j2", ctx=ctx, repo_git_summary=repo_git_summary)
        user_prompt, evidence = self._append_source_evidence(
            user_prompt, "repo_overview", source_map or {}
        )
        try:
            response = await self._call_provider(
                "repo_overview", user_prompt, str(uuid.uuid4()), target_path=repo_name
            )
        except Exception as exc:
            stub = self._stub_repo_overview(
                ctx, repo_name, f"Repository Overview: {repo_name}", repo_git_summary
            )
            page = _with_architecture_map(
                _with_package_table(
                    _with_capability_table(
                        _stub_fallback(stub, "repo_overview", exc), capabilities
                    ),
                    ctx.package_stats,
                ),
                overview_mermaid,
            )
            return self._attach_source_evidence(page, "repo_overview", evidence)
        # The overview carries its own enumerable facts: the package table and
        # the KG-derived architecture map are built from the run, not drawn by
        # the model, and both embeds are idempotent so a reused page picks them
        # up too. Appended in reading order, so what the repository does lands
        # above what it is made of, and both above the diagram.
        if capabilities:
            response = replace(
                response,
                content=embed_capability_table(
                    response.content, build_capability_table(capabilities)
                ),
            )
        if ctx.package_stats:
            response = replace(
                response,
                content=embed_package_table(
                    response.content, build_package_table(ctx.package_stats)
                ),
            )
        if overview_mermaid:
            response = replace(
                response,
                content=embed_mermaid(
                    response.content, overview_mermaid, heading="## Architecture map"
                ),
            )
        page = self._build_generated_page(
            "repo_overview",
            repo_name,
            f"Repository Overview: {repo_name}",
            response,
            compute_source_hash(user_prompt),
            GENERATION_LEVELS["repo_overview"],
        )
        return self._attach_source_evidence(page, "repo_overview", evidence)

    async def generate_architecture_diagram(
        self,
        graph: Any,
        pagerank: dict[str, float],
        community: dict[str, int],
        sccs: list[Any],
        repo_name: str,
        overview_mermaid: str | None = None,
    ) -> GeneratedPage:
        ctx = self._assembler.assemble_architecture_diagram(
            graph, pagerank, community, sccs, repo_name
        )
        if self._config.deterministic:
            return self._stub_architecture_diagram(
                ctx, repo_name, f"Architecture Diagram: {repo_name}", overview_mermaid
            )
        user_prompt = self._render("architecture_diagram.j2", ctx=ctx)
        try:
            response = await self._call_provider(
                "architecture_diagram", user_prompt, str(uuid.uuid4()), target_path=repo_name
            )
        except Exception as exc:
            # The stub embeds the same KG-derived map the model path overwrites
            # the model's diagram with, so the fallback keeps the diagram and
            # loses only the prose around it.
            stub = self._stub_architecture_diagram(
                ctx, repo_name, f"Architecture Diagram: {repo_name}", overview_mermaid
            )
            return _stub_fallback(stub, "architecture_diagram", exc)
        # Swap the LLM's free-form diagram for the deterministic KG-derived map
        # (idempotent, applies to fresh and reused content). Falls back to the
        # LLM's own mermaid when the KG can't produce one.
        if overview_mermaid:
            response = replace(
                response,
                content=embed_mermaid(
                    response.content, overview_mermaid, heading="## Architecture map"
                ),
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
        return self._structural_api_contract(
            ctx,
            parsed.file_info.path,
            structural_page_title(self._language, "api_contract", parsed.file_info.path),
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
        page_key = f"onboarding/{spec.slot}"
        ctx = spec.build_context(signals)
        if ctx is None:
            log.debug("onboarding.gate_skipped", slot=spec.slot)
            self._disabled_source_evidence(page_key, "page_not_generated")
            return None

        target = _onboarding.target_path(spec.slot)
        references = spec.evidence_references(ctx) if spec.evidence_references else ()
        if self._config.deterministic or spec.deterministic:
            # No grounding post-check: a template can only cite what the
            # context handed it, so there is nothing ungrounded to strip.
            #
            # ``spec.deterministic`` takes this path on every run, not only
            # under ``--no-prose``. The subkinds that set it are made of facts
            # the run already holds, and asking a model to restate those is how
            # a page that changed nothing comes back different — measured on
            # the overview at two calls one second apart.
            #
            # The two paths render the same template and make opposite claims
            # about the result: a ``--no-prose`` page is a page waiting for a
            # model, and a ``deterministic`` subkind's page is finished.
            page = (
                self._model_free_onboarding_page(spec, ctx, target)
                if spec.deterministic
                else self._stub_onboarding_page(spec, ctx, target)
            )
            evidence = self._disabled_source_evidence(
                page_key,
                "deterministic_generation",
                references,
            )
            return self._attach_source_evidence(page, page_key, evidence)

        evidence = self._select_source_evidence(
            page_key,
            signals.source_map,
            parsed_files=signals.parsed_files,
            references=references,
        )
        template_name = f"onboarding/{spec.template}"
        user_prompt = self._render(
            template_name,
            ctx=ctx,
            slot=spec.slot,
            exact_source_available=any(item.symbol is not None for item in evidence.included),
        )
        if evidence.rendered:
            user_prompt += evidence.rendered
        # Fold the onboarding generation version into the reuse hash so a
        # builder/template upgrade forces a one-time regen of cached pages.
        salt = _onboarding.ONBOARDING_GENERATION_VERSION
        try:
            response = await self._call_provider(
                "onboarding", user_prompt, str(uuid.uuid4()), target_path=target, source_salt=salt
            )
        except Exception as exc:
            # ``_stub_onboarding_page`` stamps the subkind metadata itself, so
            # the fallback is interchangeable with the deterministic path.
            stub = self._stub_onboarding_page(spec, ctx, target)
            page = _stub_fallback(stub, "onboarding", exc)
            return self._attach_source_evidence(page, page_key, evidence)
        # Grounding post-check: strip ungrounded path/symbol citations from the
        # output. Runs on fresh AND reused content (``response.content`` carries
        # the prior page's bytes on a cache hit), so an existing user's cached
        # page is cleaned on their next docs update.
        grounding_evidence: dict[str, str] = {}
        for item in evidence.included:
            grounding_evidence[item.path] = "\n".join(
                filter(None, (grounding_evidence.get(item.path), item.text))
            )
        cleaned, ungrounded = _onboarding.check_grounding(response.content, ctx, grounding_evidence)
        if ungrounded:
            log.info(
                "onboarding.grounding_stripped",
                slot=spec.slot,
                count=len(ungrounded),
                tokens=ungrounded[:20],
            )
            response = replace(response, content=cleaned)
        page = self._build_generated_page(
            "onboarding",
            target,
            spec.title,
            response,
            compute_source_hash(user_prompt + salt),
            GENERATION_LEVELS["onboarding"],
        )
        # Subkind discriminator lives in metadata; page_type alone is shared
        # across all six generated onboarding slots.
        page.metadata["subkind"] = spec.slot
        page.metadata["onboarding_slot"] = spec.slot
        return self._attach_source_evidence(page, page_key, evidence)

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
        return self._structural_infra_page(
            ctx,
            parsed.file_info.path,
            structural_page_title(self._language, "infra_page", parsed.file_info.path),
        )
