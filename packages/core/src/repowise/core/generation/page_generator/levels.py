"""Per-level coroutine builders for the generation orchestrator.

Each function takes the live :class:`_GenerationRun` and returns a list of
``(page_id, coroutine)`` tuples for one generation level. They read graph
metrics, selection allow-sets, and the shared context cache off the run
object. Behaviour mirrors the original inline ``generate_all`` exactly.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from repowise.core.ids import is_external

from .. import onboarding as _onboarding
from ..context_assembler import FilePageContext
from ..models import compute_page_id
from .helpers import _is_infra_file, decisions_for_files, rank_decisions

if TYPE_CHECKING:
    from ..concept_tree.vocabulary import HouseTerm
    from .orchestrate import _GenerationRun

log = structlog.get_logger(__name__)


def build_level01_coros(run: _GenerationRun) -> list[tuple[str, Any]]:
    """Level 0 (api_contract) + level 1 (symbol_spotlight), merged."""
    gen = run.gen
    # ---- Level 0: api_contract (allow-set filtered) ----
    api_files = [
        p
        for p in run.parsed_files
        if p.file_info.is_api_contract and p.file_info.path in run.sel_api_paths
    ]
    level0 = [
        (
            compute_page_id("api_contract", p.file_info.path),
            gen.generate_api_contract(p, run.source_map.get(p.file_info.path, b"")),
        )
        for p in api_files
        if run._emit(compute_page_id("api_contract", p.file_info.path))
    ]

    # ---- Level 1: symbol_spotlight (allow-set filtered) ----
    parsed_by_path: dict[str, Any] = {p.file_info.path: p for p in run.parsed_files}
    top_symbols: list[tuple[Any, Any]] = []
    for file_path, sym_name in run.selection.symbol_spotlights:
        pf = parsed_by_path.get(file_path)
        if pf is None:
            continue
        sym = next((s for s in pf.symbols if s.name == sym_name), None)
        if sym is not None:
            top_symbols.append((sym, pf))

    level1 = [
        (
            compute_page_id("symbol_spotlight", f"{pf.file_info.path}::{sym.name}"),
            gen.generate_symbol_spotlight(
                sym, pf, run.pagerank, run.graph, source_map=run.source_map
            ),
        )
        for sym, pf in top_symbols
        if run._emit(compute_page_id("symbol_spotlight", f"{pf.file_info.path}::{sym.name}"))
    ]
    return level0 + level1


def _topo_order_code_files(run: _GenerationRun) -> None:
    """Reorder ``run.code_files`` so dependencies are generated before dependents."""
    code_file_paths = [p.file_info.path for p in run.code_files]
    graph = run.graph
    try:
        import networkx as nx  # type: ignore[import]

        code_file_set = set(code_file_paths)
        dag = nx.DiGraph()
        dag.add_nodes_from(code_file_paths)
        for path_ in code_file_paths:
            if path_ in graph:
                for succ in graph.successors(path_):
                    if succ in code_file_set:
                        dag.add_edge(path_, succ)  # path_ depends on succ

        if nx.is_directed_acyclic_graph(dag):
            # topological_sort yields u before v for each edge u→v (dependents
            # before dependencies). We want leaves first, so reverse.
            topo_order = list(reversed(list(nx.topological_sort(dag))))
        else:
            condensation = nx.condensation(dag)
            topo_order_scc = list(reversed(list(nx.topological_sort(condensation))))
            scc_members: dict[int, list[str]] = {
                n: list(condensation.nodes[n]["members"]) for n in condensation.nodes
            }
            topo_order = [node for scc_id in topo_order_scc for node in scc_members[scc_id]]

        priority_index = {p: i for i, p in enumerate(code_file_paths)}
        topo_order = [p for p in topo_order if p in priority_index]
        path_to_parsed = {p.file_info.path: p for p in run.code_files}
        run.code_files = [path_to_parsed[p] for p in topo_order if p in path_to_parsed]
    except Exception:
        pass  # Keep existing priority order on any failure


async def _prefetch_dependency_summaries(run: _GenerationRun) -> None:
    """Batch-prefetch dependency summaries from the vector store in one call."""
    if run.vector_store is None:
        return
    needed_deps: set[str] = set()
    for p in run.code_files:
        path_ = p.file_info.path
        if path_ not in run.graph:
            continue
        for dep in run.graph.successors(path_):
            if is_external(dep):
                continue
            if dep in run.completed_page_summaries:
                continue
            needed_deps.add(dep)
    if not needed_deps:
        return
    try:
        batch = await run.vector_store.get_page_summaries_by_paths(list(needed_deps))
        for dep_path, payload in batch.items():
            summary = payload.get("summary") if payload else None
            if summary:
                run.completed_page_summaries[dep_path] = summary
    except Exception as exc:
        log.debug("rag.batch_dep_prefetch_failed", error=str(exc))


async def build_level2_coros(run: _GenerationRun) -> list[tuple[str, Any]]:
    """Level 2 (file_page): topo-ordered context assembly, then one renderer.

    Context is assembled for ALL code files (module pages need it). Pages are
    emitted only for files in the selection allow-set.

    Known cost on a scoped run (``only_page_ids``): this still assembles every
    code file's context even when the scope emits a handful of pages, because
    ``run.file_page_contexts`` feeds the module/SCC/layer builders. It is pure
    CPU, no LLM, and bounded by the reparse the caller already paid. Upgrade
    path if it bites on a very large repo: restrict assembly to the emitted file
    pages plus the member files of the emitted module/SCC/layer pages.
    """
    gen = run.gen
    _topo_order_code_files(run)
    await _prefetch_dependency_summaries(run)

    # One pass over the graph's symbol nodes feeds every file's call-graph /
    # heritage extraction (instead of a full node scan per file).
    from ..context.graph_intelligence import build_symbol_index

    symbol_index = build_symbol_index(run.graph)

    items: list[tuple[Any, FilePageContext]] = []
    for p in run.code_files:
        kg_file_ctx = (
            run.kg_ctx.get_file_context(p.file_info.path) if run.kg_ctx.available else None
        )
        ctx: FilePageContext = gen._assembler.assemble_file_page(
            p,
            run.graph,
            run.pagerank,
            run.betweenness,
            run.community,
            run.source_map.get(p.file_info.path, b""),
            git_meta=run.git_meta_map.get(p.file_info.path) if run.git_meta_map else None,
            page_summaries=run.completed_page_summaries,
            dead_code_findings=run.dead_code_by_file.get(p.file_info.path),
            decision_records=run.decisions_by_file.get(p.file_info.path),
            kg_context=kg_file_ctx,
            symbol_index=symbol_index,
        )
        run.file_page_contexts[p.file_info.path] = ctx
        items.append((p, ctx))

    coros: list[tuple[str, Any]] = []
    for p, ctx in items:
        path = p.file_info.path
        pid = compute_page_id("file_page", path)
        if path in run.sel_file_paths and run._emit(pid):
            coros.append((pid, gen._render_file_page(p, ctx)))
    return coros


def _scc_titles(scc_groups: list[Any], language: str | None = None) -> dict[str, str]:
    """``scc id -> title``, unique across the run's cycles.

    Names are computed here rather than per page because uniqueness is a
    property of the set: several cycles through one subsystem all describe
    themselves the same way — three of this repository's seventeen are
    "Ingestion Resolvers" — and two identical rows in the tree are
    indistinguishable to a reader.

    Resolved over the whole set even when the run emits a subset, so a scoped
    run gives a cycle the same name a full one does.
    """
    from ..concept_tree.naming import disambiguate_titles, scc_where
    from ..structural_labels import resolve_structural_labels

    kind = resolve_structural_labels(language)["circular_dependency"]
    pairs: list[tuple[str, str]] = []
    for scc_id, scc_files in scc_groups:
        where = scc_where(sorted(scc_files))
        pairs.append((f"{kind}: {where}" if where else kind, scc_id))
    # Ties break on the cycle's own id, which is a hash of its members, so the
    # discriminator is stable for an unchanged cycle.
    titles = disambiguate_titles(pairs)
    return {
        scc_id: (title if title != kind else f"{kind}: {scc_id}")
        for title, (_t, scc_id) in zip(titles, pairs, strict=True)
    }


def build_level3_coros(run: _GenerationRun) -> list[tuple[str, Any]]:
    """Level 3 (scc_page), allow-set filtered."""
    gen = run.gen
    coros: list[tuple[str, Any]] = []
    titles = _scc_titles(list(run.sel_scc_groups), gen._language)
    for scc_id, scc_files in run.sel_scc_groups:
        fc_list = [run.file_page_contexts[f] for f in scc_files if f in run.file_page_contexts]
        pid = compute_page_id("scc_page", scc_id)
        if run._emit(pid):
            coros.append(
                (pid, gen.generate_scc_page(scc_id, scc_files, fc_list, title=titles.get(scc_id)))
            )
    return coros


def _rollup_child_pages(rollup: Any, groups: list[Any]) -> list[dict]:
    """The pages a rollup links down to: its *immediate* children only.

    A page is an immediate child when the rollup's key is exactly the parent
    directory of the page's key. Immediate rather than recursive so a nested
    overview links to the next level down (a sub-overview or a leaf), not to
    every descendant leaf — otherwise ``a/b`` and ``a/b/c`` would both list the
    same leaves under ``a/b/c``. A sub-rollup is a legitimate child and is kept;
    the rollup itself is excluded. Titles are read after naming, so they match
    what the tree shows.
    """
    children = [
        g
        for g in groups
        if g.key != rollup.key and "/" in g.key and g.key.rsplit("/", 1)[0] == rollup.key
    ]
    children.sort(key=lambda g: g.key)
    return [{"title": g.display, "path": g.key} for g in children]


def build_level4_coros(run: _GenerationRun) -> list[tuple[str, Any]]:
    """Level 4 (module_page), allow-set filtered."""
    gen = run.gen
    coros: list[tuple[str, Any]] = []
    for mg in run.sel_module_groups:
        # Read from the wider set: a chapter's prose is about its whole
        # subsystem, while ``file_paths`` is the narrower, disjoint claim on who
        # documents what. They are the same list for every leaf.
        material = getattr(mg, "context_paths", ()) or mg.file_paths
        fcs = [run.file_page_contexts[fp] for fp in material if fp in run.file_page_contexts]
        if not fcs:
            # A concept group whose files all failed to build a context. The
            # partition is total, so this is a hole in the tree rather than a
            # page not worth writing, and it should be visible when it
            # happens instead of leaving the reader to notice the gap.
            log.warning(
                "module_page.skipped_no_file_contexts",
                target_path=mg.key,
                members=len(material),
            )
            continue
        page_id = compute_page_id("module_page", mg.key)
        if not run._emit(page_id):
            continue
        coros.append(
            (
                page_id,
                gen.generate_module_page(
                    mg.display,
                    mg.language,
                    fcs,
                    run.graph,
                    git_meta_map=run.git_meta_map,
                    page_summaries=run.completed_page_summaries,
                    # The module's own decisions, not the repository's. File
                    # pages already scope this way; passing the flat list here
                    # showed the first five written, which on a repo with
                    # decisions concentrated in one area is the same five on
                    # every module page -- and they are prompt context, not
                    # just rendered output.
                    decision_records=decisions_for_files(run.decisions_by_file, material),
                    dead_code_findings=[
                        d for fc in fcs for d in run.dead_code_by_file.get(fc.file_path, [])
                    ],
                    external_systems=run.external_systems,
                    community_label=mg.label,
                    community_cohesion=mg.cohesion,
                    target_path=mg.key,
                    structural_key=mg.structural_key,
                    members=list(mg.file_paths),
                    section=mg.section,
                    order=mg.order,
                    scope=getattr(mg, "scope", ""),
                    is_rollup=getattr(mg, "is_rollup", False),
                    child_pages=(
                        _rollup_child_pages(mg, run.sel_module_groups)
                        if getattr(mg, "is_rollup", False)
                        else None
                    ),
                    # Ownership is what ``file_paths`` says, not what the page's
                    # shape implies: a chapter that is also a leaf directory
                    # heads its children *and* documents its own loose files.
                    owns_files=bool(mg.file_paths),
                ),
            )
        )
    return coros


async def _module_corroboration(run: _GenerationRun) -> list[str]:
    """What the structural side calls the parts of the system.

    One string per module group: its title, plus its summary. A module group
    is cut from the dependency graph and named from the code, so a mined term
    appearing in one was arrived at twice — from the documents and from the
    structure — independently.

    Titles alone are about ninety short strings, which is too thin a net: it
    misses "Knowledge Graph" and "Code Health" while letting "Architecture"
    and "Workspace" through on an incidental word. The summaries are what make
    the corroboration mean something.

    Summaries come from this run when level 4 wrote the page, and from the
    store when it did not. Both together, because either alone is wrong: the
    run alone empties the corpus on every scoped update and the section
    vanishes from the front page, and the store alone is one generation stale
    on a full run. Never raises — a store that cannot answer costs reach, not
    a page.

    Memoised on the run. Two levels want it — the overview at 6 and the
    glossary at 8 — and it costs a batched store read, so the second caller
    reuses the first's answer rather than paying it again. A run that emits
    neither page never reaches this.
    """
    cached = getattr(run, "_module_corroboration_corpus", None)
    if cached is not None:
        return cached

    groups = run.sel_module_groups
    if not groups:
        run._module_corroboration_corpus = []
        return []

    summaries: dict[str, str] = {}
    missing: list[str] = []
    for mg in groups:
        written = run.completed_page_summaries.get(mg.key)
        if written:
            summaries[mg.key] = written
        else:
            missing.append(mg.key)

    if missing and run.vector_store is not None:
        try:
            batch = await run.vector_store.get_page_summaries_by_paths(missing)
        except Exception as exc:
            # Reach, not correctness. Said out loud because a quietly thinner
            # corpus reads downstream as "the structure does not name this".
            log.warning(
                "generation.overview_corroboration_store_read_failed",
                repo_name=run.repo_name,
                wanted=len(missing),
                error=str(exc),
            )
        else:
            for path, payload in batch.items():
                summary = (payload or {}).get("summary")
                if summary:
                    summaries[path] = summary

    log.info(
        "generation.overview_corroboration_corpus",
        groups=len(groups),
        from_this_run=sum(1 for mg in groups if run.completed_page_summaries.get(mg.key)),
        with_summary=len(summaries),
    )
    corpus = [f"{mg.display}\n{summaries.get(mg.key, '')}" for mg in groups]
    run._module_corroboration_corpus = corpus
    return corpus


async def build_level6_coros(run: _GenerationRun) -> list[tuple[str, Any]]:
    """Level 6 (repo_overview).

    The overview carries the architecture map. That map used to sit on a page
    of its own, which described the same repository at the same altitude in the
    same words — the two shared roughly a quarter of their vocabulary, so a
    reader meeting both read one thing twice. The diagram is what that page
    uniquely had, so it moved here and the page retired; its id redirects.
    """
    from ..architecture_mermaid import build_overview_mermaid
    from ..overview_tables import select_capabilities

    gen = run.gen
    overview_mermaid = build_overview_mermaid(run.kg_ctx)
    if not overview_mermaid:
        # The retired page drew its own diagram when the graph could not supply
        # one, so this used to degrade to a worse map rather than to none. Now
        # the wiki simply ships without one, which is worth knowing about.
        log.warning(
            "generation.overview_architecture_map_empty",
            repo_name=run.repo_name,
        )
    coros: list[tuple[str, Any]] = []
    if run._emit(compute_page_id("repo_overview", run.repo_name)):
        # What the repository calls its own capabilities, in its own words.
        # Mined once per run and shared with level 8. A term reaches the page
        # only when the structural side names it too, so the front page never
        # carries a word the documents used and the graph never confirmed.
        #
        # Module *groups*, not written module pages: a group is cut and named
        # on every run, so a scoped run that regenerates the overview alone
        # selects the same rows as a full one.
        capabilities = select_capabilities(_mine_house_terms(run), await _module_corroboration(run))
        if not capabilities:
            # No table beats an empty one, but a front-page section that
            # quietly stops appearing is the failure shape this repository has
            # shipped before. Said out loud with the counts that explain it.
            log.info(
                "generation.overview_capability_table_absent",
                repo_name=run.repo_name,
                module_groups=len(run.sel_module_groups),
            )
        coros.append(
            (
                compute_page_id("repo_overview", run.repo_name),
                gen.generate_repo_overview(
                    run.repo_structure,
                    run.pagerank,
                    run.sccs,
                    run.community,
                    git_meta_map=run.git_meta_map,
                    graph_builder=run.graph_builder,
                    repo_name=run.repo_name,
                    external_systems=run.external_systems,
                    # Repo-wide scope is right here; only the choice of ten was
                    # list position.
                    decision_records=rank_decisions(run.decisions_all)[:10],
                    overview_mermaid=overview_mermaid,
                    source_map=run.source_map,
                    # Per-package file counts come from the files this run
                    # actually parsed, not from the package manifests, so a
                    # directory the walker skipped reads as zero rather than
                    # going unmentioned.
                    parsed_files=run.parsed_files,
                    capabilities=capabilities,
                ),
            )
        )
    return coros


def build_level7_coros(run: _GenerationRun) -> list[tuple[str, Any]]:
    """Level 7 (infra_page), allow-set filtered."""
    gen = run.gen
    infra_files = [
        p for p in run.parsed_files if _is_infra_file(p) and p.file_info.path in run.sel_infra_paths
    ]
    return [
        (
            compute_page_id("infra_page", p.file_info.path),
            gen.generate_infra_page(p, run.source_map.get(p.file_info.path, b"")),
        )
        for p in infra_files
        if run._emit(compute_page_id("infra_page", p.file_info.path))
    ]


def _mine_house_terms(run: _GenerationRun) -> tuple[HouseTerm, ...]:
    """The repository's own vocabulary, read once per run.

    Mined here rather than inside a subkind because reading it walks the
    repository: once per run is a cost, once per slot is the same cost eight
    times over for the same answer. Two levels want it now — the overview at
    6 and onboarding at 8 — so the result is memoised on the run rather than
    the walk being paid twice. A run that emits neither still pays nothing,
    because neither caller reaches this.

    ``repo_path`` is optional on every generation entry point, and a run
    without one has nothing to read. That is reported rather than absorbed —
    an empty vocabulary from a repository that was never opened looks exactly
    like an empty vocabulary from a repository that writes about nothing, and
    the two want opposite responses.
    """
    from ..concept_tree.vocabulary import extract_house_terms
    from ..report import record_house_terms

    cached = getattr(run, "_house_terms", None)
    if cached is not None:
        return cached

    if not run.repo_path:
        log.warning(
            "onboarding.house_terms_skipped",
            repo_name=run.repo_name,
            reason="no_repo_path",
        )
        record_house_terms(None)
        run._house_terms = ()
        return ()

    # The names the codebase defines. A term matching one may be rendered in
    # backticks; a coined term may not, because the grounding pass strips
    # backticks off any token it cannot resolve to a symbol.
    known_symbols = {sym.name for pf in run.parsed_files for sym in pf.symbols if sym.name}
    terms = tuple(extract_house_terms(Path(run.repo_path), known_symbols=known_symbols))
    record_house_terms(terms)
    if not terms:
        log.warning(
            "onboarding.house_terms_empty",
            repo_name=run.repo_name,
            known_symbols=len(known_symbols),
        )
    else:
        log.info(
            "onboarding.house_terms_mined",
            repo_name=run.repo_name,
            terms=len(terms),
            top=[t.term for t in terms[:8]],
        )
    run._house_terms = terms
    return terms


async def build_level8_coros(run: _GenerationRun) -> list[tuple[str, Any]]:
    """Level 8 (curated onboarding collection)."""
    gen = run.gen
    coros: list[tuple[str, Any]] = []
    if not getattr(run.config, "enable_onboarding", True):
        for page_key in run.config.source_evidence_files:
            if page_key.startswith("onboarding/"):
                gen._disabled_source_evidence(page_key, "onboarding_disabled")
        return coros
    specs = _onboarding.iter_specs()
    if not specs:
        return coros
    if run.on_subphase is not None:
        with contextlib.suppress(Exception):
            run.on_subphase("onboarding", len(specs))
    # Which pages this run will actually write, decided before anything is
    # assembled for them. ``_emit`` carries a side effect (a resumed run
    # records the ids it is protecting from the stale sweep), so it is called
    # exactly once per spec here and not again below. Assembling the signals
    # costs a walk of the repository, and a scoped run that asked for one file
    # page should not pay it to emit nothing.
    emitted: list[tuple[str, Any]] = []
    for spec in specs:
        page_id = compute_page_id("onboarding", _onboarding.target_path(spec.slot))
        if run._emit(page_id):
            emitted.append((page_id, spec))
    if not emitted:
        return coros

    kg_layers: tuple[dict, ...] = ()
    kg_tour_steps: tuple[dict, ...] = ()
    if run.kg_ctx and run.kg_ctx.available:
        kg_layers = tuple(run.kg_ctx.get_layers())
        kg_tour_steps = tuple(run.kg_ctx.get_tour())

    # The corpus costs a batched store read, so only a run emitting a subkind
    # that declared it wants one pays for it. Memoised, so a run that also
    # emits the overview builds it once for both.
    module_corroboration = (
        tuple(await _module_corroboration(run))
        if any(spec.needs_module_corroboration for _page_id, spec in emitted)
        else ()
    )

    signals = _onboarding.OnboardingSignals(
        repo_name=run.repo_name,
        repo_structure=run.repo_structure,
        parsed_files=tuple(run.parsed_files),
        source_map=run.source_map,
        graph_builder=run.graph_builder,
        pagerank=run.pagerank,
        betweenness=run.betweenness,
        community=run.community,
        sccs=tuple(run.sccs),
        git_meta_map=run.git_meta_map,
        dead_code_by_file=run.dead_code_by_file,
        decisions_all=tuple(run.decisions_all),
        external_systems=tuple(run.external_systems),
        completed_page_summaries=dict(run.completed_page_summaries),
        kg_layers=kg_layers,
        kg_tour_steps=kg_tour_steps,
        tour_stops=tuple(run.tour_stops),
        layer_order=tuple(run.layer_order),
        house_terms=_mine_house_terms(run),
        module_corroboration=module_corroboration,
    )
    for page_id, spec in emitted:
        coros.append((page_id, gen.generate_onboarding_page(spec, signals)))
    return coros
