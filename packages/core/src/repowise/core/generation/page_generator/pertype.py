"""Per-page-type generation methods, mixed into :class:`PageGenerator`.

Each method assembles a context, renders its Jinja template into a user
prompt, calls the provider, and wraps the response in a ``GeneratedPage``.
They are grouped here (rather than inline on the generator) purely to keep
each module under the project's 400-line ceiling.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import structlog

from repowise.core.ingestion.git_indexer._constants import PRIOR_DEFECT_WINDOW_LABEL
from repowise.core.ingestion.models import ParsedFile, RepoStructure
from repowise.core.providers.llm.error_hints import describe_provider_error

from .. import onboarding as _onboarding
from ..agent_digest import MODULE_SIGNALS_KEY, module_signals, split_questions
from ..architecture_map import SystemMap
from ..architecture_mermaid import (
    apply_names,
    embed_system_map,
    naming_payload,
    split_names,
    system_map_section,
)
from ..context.assembler import build_concept_index
from ..context.module_facts import CONNECTIONS_HEADING, connections_markdown, module_base
from ..context_assembler import FilePageContext
from ..models import (
    GENERATION_LEVELS,
    STUB_FALLBACK_ERROR,
    STUB_FALLBACK_HINT,
    STUB_PAGE_CONFIDENCE,
    GeneratedPage,
    compute_page_id,
    compute_source_hash,
)
from ..nav_titles import NAV_TITLE_KEY, split_nav_title
from ..overview_tables import (
    Capability,
    build_capability_table,
    build_package_table,
    embed_capability_table,
    embed_package_table,
    embed_section,
)
from ..page_sources import lint_sources, module_page_sources
from ..prose import unwrap_markdown_links
from ..report import ORIENTATION_PROSE_WORD_BUDGET
from ..structural_labels import structural_page_title
from ..styles.registry import DEFAULT_STYLE
from .reuse import files_digest, group_material, model_reuse_key

log = structlog.get_logger(__name__)

#: A ``Sources:`` line written straight under its paragraph renders as part of
#: it; a blank line keeps it on its own.
_SOURCES_GLUED = re.compile(r"(?<=\S)[ \t]*\n(?=Sources: )")

#: The front page's prose range under the default style: the first-time
#: reader's tour, with room for the parts narrative.
_OVERVIEW_WORDS = (700, ORIENTATION_PROSE_WORD_BUDGET)
#: Marks an onboarding context the caller did not build.
_BUILD_CONTEXT = object()


def _git_summary(git_meta_map: dict[str, dict] | None) -> dict | None:
    """Churn facts for the keyless overview, or ``None`` with no git metadata."""
    if not git_meta_map:
        return None
    metas = list(git_meta_map.values())
    top_churn = sorted(metas, key=lambda m: m.get("commit_count_90d", 0), reverse=True)[:3]
    oldest = min(
        (m for m in metas if m.get("first_commit_at")),
        key=lambda m: m["first_commit_at"],
        default=None,
    )
    return {
        "hotspot_count": sum(1 for m in metas if m.get("is_hotspot")),
        "stable_count": sum(1 for m in metas if m.get("is_stable")),
        "top_churn_files": [m.get("file_path", "") for m in top_churn],
        "oldest_file": oldest.get("file_path", "") if oldest else "",
        "oldest_file_age_days": oldest.get("age_days", 0) if oldest else 0,
    }


def _overview_subject(system_map: SystemMap | None, material: Any) -> str:
    """Digest of what the overview is written from besides evidence files.

    The repository's name, the system map it names, and the source evidence it
    quotes are its subject: RAG drift alone reuses the prose, and a changed
    map or evidence file regenerates it (issue #1089). The walkthroughs, part
    summaries and documentation it is written from count too.
    """
    return hashlib.sha256(
        (naming_payload(system_map) if system_map else "").encode()
        + (material.digest_text() if material else "").encode()
    ).hexdigest()


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
    hint = describe_provider_error(exc)
    if hint is not None:
        page.metadata[STUB_FALLBACK_HINT] = hint.as_dict()
    page.confidence = STUB_PAGE_CONFIDENCE
    log.warning(
        "page_generation.stub_fallback",
        page_type=page_type,
        target_path=page.target_path,
        error=str(exc),
    )
    return page


#: Page metadata key holding the model's names for the system map, so a reused
#: or fallback page recompiles the current structure without another call.
SYSTEM_MAP_NAMES = "system_map_names"


def _with_system_map(
    page: GeneratedPage,
    system_map: SystemMap | None,
    repo_name: str,
    names: dict | None = None,
) -> GeneratedPage:
    """Compile the system map from the current structure into a built page.

    Every path carries it, the stub and the outage fallback too: a provider
    outage should cost the prose around the diagram, not the diagram. The map
    is recompiled on every render, so a structure or compiler change reaches a
    reused page; the names are the only model output it keeps.
    """
    section = None
    if system_map:
        section = system_map_section(apply_names(system_map, names), repo_name)
        if names:
            page.metadata[SYSTEM_MAP_NAMES] = names
    page.content = embed_system_map(page.content, section)
    return page


def _with_package_table(page: GeneratedPage, package_stats: list[dict]) -> GeneratedPage:
    """Embed the package table into an already-built page.

    Same reasoning as the system map, and the same three paths: the model
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
    ``_assembler``, ``_render``, ``_call_provider`` and ``_build_generated_page``
    (all supplied by :class:`PageGenerator`).
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
        graph: Any,
    ) -> GeneratedPage:
        ctx = self._assembler.assemble_symbol_spotlight(symbol, parsed, graph)
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
            # Titled by the target the page is keyed on, not by the dotted
            # path built from every directory above the file. That path is
            # synthetic; a real path beside a real name is what a reader
            # scanning the tree can place.
            structural_page_title(self._language, "symbol_spotlight", target),
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
        packages: list[dict] | None = None,
        public_api: list[dict] | None = None,
        parsed_files: dict[str, Any] | None = None,
        source_map: dict[str, bytes] | None = None,
        execution_flows: Sequence[Any] = (),
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
            packages=packages,
            public_api=public_api,
            parsed_files=parsed_files,
            source_map=source_map,
            execution_flows=execution_flows,
            target_path=target_path,
            full_material=self._style.name == DEFAULT_STYLE,
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

        def _with_digest(page: GeneratedPage) -> GeneratedPage:
            """Split what the page carries for agents off its body.

            The identifiers, public API and git signals are rendered here from
            the index, not asked of the model: material in a prompt is
            material a model may reformat, abbreviate or drop, and appended
            after generation every name and path is the index's own. After
            ``_build_generated_page`` too, so the page summary is still drawn
            from the model's opening.

            The package table of a roll-up stays in the body: it is how a
            reader finds the packages the page covers. Replaced, not appended,
            so a page reused from a prior run keeps one table.
            """
            if ctx.packages:
                table = self._render("_package_table.j2", style_prefix=False, rows=ctx.packages)
                page.content = embed_package_table(page.content or "", table.strip())
            page.content, questions = split_questions(page.content)
            rows, omitted = build_concept_index(file_contexts)
            signals = module_signals(ctx, module_git_summary)
            if signals:
                page.metadata[MODULE_SIGNALS_KEY] = signals
            digest = self._render(
                "_agent_digest.j2",
                style_prefix=False,
                questions=questions,
                public_api=ctx.public_api,
                concept_rows=rows,
                concept_omitted=omitted,
                signals=signals,
                fix_window=PRIOR_DEFECT_WINDOW_LABEL,
            )
            page.digest = re.sub(r"\n{3,}", "\n\n", digest).strip()
            return page

        if self._config.deterministic:

            page = self._stub_module_page(ctx, page_target, title)
            return _stamp_concept(_with_digest(page))
        user_prompt = self._render("module_page.j2", ctx=ctx, word_budget=ctx.words)
        # The page's subject is its group: the membership ``structural_key``
        # hashes, plus each member file's bytes, so an unchanged group reuses
        # its prose across RAG drift while an edited member regenerates it
        # (issue #1089). Folded with the renderer fingerprint, so a
        # prompt/template/style upgrade still forces a one-time regen.
        reuse_key = model_reuse_key(
            self,
            group_material(structural_key, members, source_map, ctx.child_pages),
            "module_page",
            "module_page.j2",
        )
        try:
            response = await self._call_provider(
                "module_page",
                user_prompt,
                str(uuid.uuid4()),
                target_path=page_target,
                reuse_content_hash=reuse_key,
            )
        except Exception as exc:
            stub = self._stub_module_page(ctx, page_target, title)
            return _stamp_concept(_with_digest(_stub_fallback(stub, "module_page", exc)))
        # The sidebar name the page picked for itself, in the same call. Off
        # the body before anything reads its opening.
        # A copy: the provider cache holds this response, and a later hit must
        # still carry the line.
        nav_title, body = split_nav_title(response.content)
        response = replace(response, content=body)
        page = self._build_generated_page(
            "module_page",
            page_target,
            title,
            response,
            compute_source_hash(user_prompt),
            GENERATION_LEVELS["module_page"],
            content_hash=reuse_key,
        )
        # Citations of a missing file, or one its section never names, go.
        # Diagrams are checked for every page of the run in ``sanitize_pages``.
        known_paths = [n for n in graph if "::" not in n]
        page.content = lint_sources(
            unwrap_markdown_links(page.content),
            base=module_base(ctx.files),
            members=ctx.files,
            known_paths=known_paths,
            symbols_by_file={fc.file_path: [s["name"] for s in fc.symbols] for fc in file_contexts},
        )
        # The functions each surviving citation is about, with their spans.
        cited = module_page_sources(
            page.content, base=module_base(ctx.files), members=ctx.files,
            known_paths=known_paths, file_contexts=file_contexts,
        )
        if cited:
            page.metadata["sources"] = cited
        if nav_title:
            page.metadata[NAV_TITLE_KEY] = nav_title
        page.content = _SOURCES_GLUED.sub("\n\n", page.content)
        page.content = embed_section(
            page.content, CONNECTIONS_HEADING, connections_markdown(ctx.facts or {})
        )
        return _stamp_concept(_with_digest(page))

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
        system_map: SystemMap | None = None,
        source_map: dict[str, bytes] | None = None,
        parsed_files: list[ParsedFile] | None = None,
        capabilities: Sequence[Capability] = (),
        material: Any = None,
        structural_only: bool = False,
    ) -> GeneratedPage:
        """Write the front page; *structural_only* renders the keyless page this run."""
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
            material=material,
        )
        repo_git_summary = _git_summary(git_meta_map)
        if not repo_name:
            repo_name = getattr(repo_structure, "name", None) or "repo"
        prior = self._prior_pages.get(compute_page_id("repo_overview", repo_name))
        prior_names = dict(prior.metadata).get(SYSTEM_MAP_NAMES) if prior else None
        if self._config.deterministic or structural_only:
            stub = self._stub_repo_overview(
                ctx, repo_name, f"Repository Overview: {repo_name}", repo_git_summary
            )
            # Stored names survive a keyless render, for the next keyed one to reuse.
            page = _with_system_map(
                _with_package_table(_with_capability_table(stub, capabilities), ctx.package_stats),
                system_map,
                repo_name,
                prior_names,
            )
            selection = self._disabled_source_evidence("repo_overview", "deterministic_generation")
            return self._attach_source_evidence(page, "repo_overview", selection)
        user_prompt = self._render(
            "repo_overview.j2",
            ctx=ctx,
            word_budget=_OVERVIEW_WORDS,
            system_map_payload=naming_payload(system_map) if system_map else "",
        )
        user_prompt, evidence = self._append_source_evidence(
            user_prompt, "repo_overview", source_map or {}
        )
        evidence_files = files_digest((item.path for item in evidence.included), source_map or {})
        reuse_key = model_reuse_key(
            self,
            f"{repo_name}|{_overview_subject(system_map, material)}|{evidence_files}",
            "repo_overview",
            "repo_overview.j2",
        )
        try:
            response = await self._call_provider(
                "repo_overview",
                user_prompt,
                str(uuid.uuid4()),
                target_path=repo_name,
                reuse_content_hash=reuse_key,
            )
        except Exception as exc:
            stub = self._stub_repo_overview(
                ctx, repo_name, f"Repository Overview: {repo_name}", repo_git_summary
            )
            page = _with_system_map(
                _with_package_table(
                    _with_capability_table(
                        _stub_fallback(stub, "repo_overview", exc), capabilities
                    ),
                    ctx.package_stats,
                ),
                system_map,
                repo_name,
                prior_names,
            )
            return self._attach_source_evidence(page, "repo_overview", evidence)
        # A fresh reply carries the names block; a reused one takes the names
        # the stored page kept.
        prose, names = split_names(response.content)
        response = replace(response, content=prose)
        names = names or prior_names
        overview_grounding_evidence: dict[str, str] = {}
        for item in evidence.included:
            overview_grounding_evidence[item.path] = "\n".join(
                filter(None, (overview_grounding_evidence.get(item.path), item.text))
            )
        repository_paths = tuple(
            parsed.file_info.path
            for parsed in parsed_files or ()
            if getattr(getattr(parsed, "file_info", None), "path", None)
        ) + tuple((source_map or {}).keys())
        cleaned, ungrounded = _onboarding.check_grounding(
            response.content,
            (ctx, repository_paths),
            overview_grounding_evidence,
        )
        if cleaned != response.content:
            response = replace(response, content=cleaned)
        if ungrounded:
            log.info(
                "repo_overview.grounding_stripped",
                count=len(ungrounded),
                tokens=ungrounded[:20],
            )
        # The written page carries its own parts table and defined terms, from
        # the same facts, so only the system map is embedded after it. The
        # capability and package tables stay on the keyless page.
        page = self._build_generated_page(
            "repo_overview",
            repo_name,
            f"Repository Overview: {repo_name}",
            response,
            compute_source_hash(user_prompt),
            GENERATION_LEVELS["repo_overview"],
            content_hash=reuse_key,
        )
        page = _with_system_map(page, system_map, repo_name, names)
        return self._attach_source_evidence(page, "repo_overview", evidence)


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
        *,
        ctx: Any = _BUILD_CONTEXT,
        next_title: str | None = None,
    ) -> GeneratedPage | None:
        """Generate one onboarding page from a registered subkind spec.

        Returns ``None`` when the subkind's gate fails (``build_context``
        returned ``None``): the slot is silently skipped for this repo. *ctx*
        is the context when the caller built it already; *next_title* is the
        page a reader opens after this one, linked at the end.
        """
        if ctx is _BUILD_CONTEXT:
            ctx = spec.build_context(signals)
        page = await self._onboarding_page(spec, signals, ctx)
        if page is not None and (next_title or "\nNext: **" in page.content):
            page.content = _onboarding.with_next_link(page.content, next_title)
        return page

    async def _onboarding_page(
        self, spec: _onboarding.SubkindSpec, signals: _onboarding.OnboardingSignals, ctx: Any
    ) -> GeneratedPage | None:
        page_key = f"onboarding/{spec.slot}"
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
            word_budget=getattr(ctx, "word_budget", None),
            exact_source_available=any(item.symbol is not None for item in evidence.included),
        )
        if evidence.rendered:
            user_prompt += evidence.rendered
        # Fold the onboarding generation version into the reuse hash so a
        # builder/template upgrade forces a one-time regen of cached pages.
        salt = _onboarding.ONBOARDING_GENERATION_VERSION
        # Subject: the slot's evidence files. Their raw bytes are the
        # volatile input (they are what the page narrates), and they are
        # stable across runs when unchanged — the one part of the prompt that
        # must not silently age out (issue #1089). The slot's own name rides
        # along so two slots sharing a file still get distinct keys.
        evidence_files = files_digest(
            (item.path for item in evidence.included), signals.source_map
        )
        reuse_key = model_reuse_key(
            self,
            spec.slot + "|" + evidence_files,
            "onboarding",
            template_name,
        )
        try:
            response = await self._call_provider(
                "onboarding",
                user_prompt,
                str(uuid.uuid4()),
                target_path=target,
                source_salt=salt,
                reuse_content_hash=reuse_key,
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
            content_hash=reuse_key,
        )
        # Subkind discriminator lives in metadata; page_type alone is shared
        # across every generated onboarding slot.
        page.metadata.update(_onboarding.slot_metadata(spec.slot))
        return self._attach_source_evidence(page, page_key, evidence)

    @staticmethod
    def _tag_promoted_pages(pages: list[GeneratedPage]) -> None:
        """Tag promoted pages (the repo overview) with their onboarding slot.

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
