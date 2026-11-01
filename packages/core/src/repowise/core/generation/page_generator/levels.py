"""Per-level coroutine builders for the generation orchestrator.

Each function takes the live :class:`_GenerationRun` and returns a list of
``(page_id, coroutine)`` tuples for one generation level. They read graph
metrics, selection allow-sets, and the shared context cache off the run
object. Behaviour mirrors the original inline ``generate_all`` exactly.
"""

from __future__ import annotations

import contextlib
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

import structlog

from repowise.core.ids import is_external
from repowise.core.ingestion.package_roots import package_roots_from_paths

from ...analysis.public_api import compute_public_api
from .. import onboarding as _onboarding
from ..concept_tree.grouping import package_file_counts
from ..context_assembler import FilePageContext
from ..models import compute_page_id
from ..page_tree import is_module_head, nearest_head
from .helpers import (
    _extract_summary,
    _is_infra_file,
    decisions_for_files,
    indexed_parsed_files,
    rank_decisions,
)

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
            gen.generate_symbol_spotlight(sym, pf, run.graph),
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


def _heads(groups: list[Any]) -> set[str]:
    """The groups other module pages nest under, by the rule the page tree uses."""
    return {
        g.key
        for g in groups
        if is_module_head(g.key, getattr(g, "file_paths", ()), bool(getattr(g, "is_rollup", False)))
    }


def _children_of(key: str, groups: list[Any], heads: set[str]) -> list[Any]:
    """The pages nested directly under *key*: those it is the nearest head of.

    The same rule ``assign_page_tree`` nests by, so the parts a page describes
    match the tree: the next level down (a sub-overview or a leaf), not every
    descendant, and a leaf several directories below a package chapter with no
    chapter in between still counts. The page itself is excluded.
    """
    return [g for g in groups if g.key != key and nearest_head(g.key, heads) == key]


#: A map page with fewer parts than this looks through its biggest part to the
#: parts below it, so a thin layer (``src/``, a single package directory) does
#: not leave the page mapping one box.
_MIN_PARTS = 4
#: How much of a part page's opening a map page reads, and how many of the
#: names other code imports from the part.
_PART_SUMMARY_CHARS = 450
_PART_NAMES = 3


def _part_pages(rollup: Any, groups: list[Any], heads: set[str]) -> list[Any]:
    """The groups a map page describes: its child pages, looking through thin layers."""
    parts = _children_of(rollup.key, groups, heads)
    while 0 < len(parts) < _MIN_PARTS:
        deeper = [(len(_children_of(g.key, groups, heads)), g.key, g) for g in parts]
        size, _, biggest = max(deeper, key=lambda t: (t[0], t[1]))
        if not size:
            break
        parts = [g for g in parts if g is not biggest] + _children_of(biggest.key, groups, heads)
    return sorted(parts, key=lambda g: g.key)


def _part_summary(run: _GenerationRun, key: str) -> str:
    """How a part's page opens: this run's page, else the stored one, else nothing."""
    content = run.module_page_content.get(key)
    if content is None:
        prior = run.gen._prior_pages.get(compute_page_id("module_page", key))
        content = prior.content if prior is not None else ""
    return _extract_summary(content, max_chars=_PART_SUMMARY_CHARS)


def _imported_names(graph: Any, files: list[str]) -> list[str]:
    """The names code outside *files* imports from them most often."""
    members = set(files)
    counts: Counter[str] = Counter()
    for target in members:
        if target not in graph:
            continue
        for source, _, data in graph.in_edges(target, data=True):
            if data.get("edge_type") == "imports" and source not in members:
                counts.update(data.get("imported_names") or ())
    return [n for n, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:_PART_NAMES]]


def _part_rows(run: _GenerationRun, rollup: Any, heads: set[str]) -> list[dict]:
    """What a map page knows of each part: title, path, size, opening and key names."""
    rows = []
    for g in _part_pages(rollup, run.sel_module_groups, heads):
        files = list(getattr(g, "context_paths", ()) or g.file_paths)
        row = {"title": g.display, "path": g.key, "files": len(files)}
        summary = _part_summary(run, g.key)
        names = _imported_names(run.graph, files)
        rows.append({**row, **({"summary": summary} if summary else {}), **({"names": names} if names else {})})
    return rows


def _is_empty_module(file_contexts: list[Any], has_parts: bool) -> bool:
    """Nothing to write about: no parts, and no file defines or imports anything.

    A 0-byte ``__init__.py`` grouped on its own got a page of sentences saying
    there was nothing to say. Any symbol or import keeps the page.
    """
    return not has_parts and all(not fc.symbols and not fc.dependencies for fc in file_contexts)


def module_waves(groups: list[Any]) -> list[list[Any]]:
    """Module groups in the order their pages are written: leaves, then maps deepest first.

    A map page reads how its parts' pages open, so every part is written in an
    earlier wave than the map above it.
    """
    heads = _heads(groups)
    parents = {nearest_head(g.key, heads) for g in groups}
    leaves = [g for g in groups if g.key not in parents]
    by_depth: dict[int, list[Any]] = {}
    for g in groups:
        if g.key in parents:
            by_depth.setdefault(len(PurePosixPath(g.key).parts), []).append(g)
    return [w for w in [leaves, *(by_depth[d] for d in sorted(by_depth, reverse=True))] if w]


def _execution_flows(graph_builder: Any) -> list[Any]:
    """The run's traced execution flows, for module pages that host one."""
    try:
        return list(graph_builder.execution_flows().flows)
    except Exception as exc:
        log.warning("module_page.execution_flows_unavailable", error=str(exc))
        return []


def build_level4_coros(run: _GenerationRun, groups: list[Any] | None = None) -> list[tuple[str, Any]]:
    """Level 4 (module_page) for *groups* (default: every selected group), allow-set filtered."""
    gen = run.gen
    coros: list[tuple[str, Any]] = []
    parsed_by_path = indexed_parsed_files(run.graph_builder, run.parsed_files)
    package_roots = package_roots_from_paths(set(parsed_by_path))
    flows = _execution_flows(run.graph_builder)
    heads = _heads(run.sel_module_groups)
    for mg in run.sel_module_groups if groups is None else groups:
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
        is_rollup = bool(getattr(mg, "is_rollup", False))
        parts = _part_rows(run, mg, heads)
        if _is_empty_module(fcs, is_rollup or bool(parts)):
            log.info("module_page.skipped_empty", target_path=mg.key, members=len(material))
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
                    is_rollup=is_rollup,
                    child_pages=parts or None,
                    # Ownership is what ``file_paths`` says, not what the page's
                    # shape implies: a chapter that is also a leaf directory
                    # heads its children *and* documents its own loose files.
                    owns_files=bool(mg.file_paths),
                    packages=[
                        {"path": path, "files": count}
                        for path, count in package_file_counts(mg.file_paths, mg.packages)
                    ],
                    public_api=compute_public_api(material, parsed_by_path, package_roots),
                    parsed_files=parsed_by_path,
                    source_map=run.source_map,
                    execution_flows=flows,
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
    summaries = await _module_summaries(run)
    corpus = [f"{mg.display}\n{summaries.get(mg.key, '')}" for mg in groups]
    run._module_corroboration_corpus = corpus
    return corpus


async def _module_summaries(run: _GenerationRun) -> dict[str, str]:
    """``group key -> summary`` of each module page, this run's first, else the store's.

    Memoised on the run: the corroboration corpus and the overview's parts
    table both read it, and it costs a batched store read.
    """
    cached = getattr(run, "_module_summaries_cache", None)
    if cached is not None:
        return cached
    groups = run.sel_module_groups
    if not groups:
        run._module_summaries_cache = {}
        return {}

    summaries = {
        mg.key: run.completed_page_summaries[mg.key]
        for mg in groups
        if run.completed_page_summaries.get(mg.key)
    }
    missing = [mg.key for mg in groups if mg.key not in summaries]
    if missing and run.vector_store is not None:
        summaries.update(await _stored_summaries(run, missing))

    log.info(
        "generation.overview_corroboration_corpus",
        groups=len(groups),
        from_this_run=sum(1 for mg in groups if run.completed_page_summaries.get(mg.key)),
        with_summary=len(summaries),
    )
    run._module_summaries_cache = summaries
    return summaries


async def _stored_summaries(run: _GenerationRun, missing: list[str]) -> dict[str, str]:
    """Summaries the store holds for pages this run did not write. Never raises."""
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
        return {}
    return {
        path: summary
        for path, payload in batch.items()
        if (summary := (payload or {}).get("summary"))
    }


def _doc_sources(run: _GenerationRun) -> dict[str, bytes]:
    """The repository's documentation files, read once per run for levels 6 and 8."""
    from ..onboarding.docs import doc_sources

    cached = getattr(run, "_doc_sources_cache", None)
    if cached is None:
        root = Path(run.repo_path) if run.repo_path else None
        cached = run._doc_sources_cache = doc_sources(root, run.source_map)
    return cached


def _surfaces(run: _GenerationRun) -> Any:
    """What a user types or calls, read once per run for levels 6 and 8."""
    from ..flow_entries import line_reader
    from ..onboarding.docs import doc_sections
    from ..onboarding.surface import surfaces

    cached = getattr(run, "_surfaces_cache", None)
    if cached is None:
        sections = doc_sections(_doc_sources(run))
        read = line_reader(run.repo_path, run.source_map)
        cached = surfaces(run.graph_builder.graph(), read, run.repo_path, sections)
        run._surfaces_cache = cached
    return cached


async def _overview_material(run: _GenerationRun, capabilities: list[Any]) -> Any:
    """The front page's material; ``None`` when building it fails (the page keeps its map)."""
    from .front_page import overview_material, part_edges, run_journeys, top_parts

    try:
        parts = top_parts(run.sel_module_groups, await _module_summaries(run), run.source_map)
        return overview_material(
            _doc_sources(run),
            surfaces=_surfaces(run),
            journeys=run_journeys(run),
            parts=parts,
            edges=part_edges(run.graph_builder.graph(), run.sel_module_groups, parts),
            terms=capabilities,
        )
    except Exception as exc:
        # The page still renders, with its map; said out loud because a front
        # page without its README and parts is the failure worth hearing about.
        log.warning("generation.overview_material_failed", error=str(exc))
        return None


async def build_level6_coros(run: _GenerationRun) -> list[tuple[str, Any]]:
    """Level 6 (repo_overview).

    The overview carries the system map, built from the run's structure. It
    used to sit on a page of its own that described the same repository at the
    same altitude; that page retired and its id redirects here.
    """
    from ..overview_tables import select_capabilities
    from .system_map import build_run_system_map

    gen = run.gen
    coros: list[tuple[str, Any]] = []
    if run._emit(compute_page_id("repo_overview", run.repo_name)):
        system_map = build_run_system_map(run)
        if system_map is None:
            # Said out loud: a front page that quietly loses its diagram is the
            # failure shape worth hearing about.
            log.warning("generation.overview_system_map_empty", repo_name=run.repo_name)
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
        material, unavailable = await _material_or_unavailable(run, capabilities)
        if unavailable and _has_written_overview(gen, run.repo_name):
            # Keep the written page rather than let a model describe a
            # repository with no README and no parts; the next run retries.
            return coros
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
                    system_map=system_map,
                    source_map=run.source_map,
                    # Per-package file counts come from the files this run
                    # actually parsed, not from the package manifests, so a
                    # directory the walker skipped reads as zero rather than
                    # going unmentioned.
                    parsed_files=run.parsed_files,
                    capabilities=capabilities,
                    # What a first-time reader needs: the README, the commands
                    # and walkthroughs, the parts. Only a model writes from it,
                    # so a keyless run does not build it.
                    material=material,
                    # Material that failed to build is not an empty repository:
                    # the page renders structurally and stays unwritten.
                    structural_only=unavailable,
                ),
            )
        )
    return coros


async def _material_or_unavailable(run: _GenerationRun, capabilities: list[Any]) -> tuple[Any, bool]:
    """``(material, failed)``: none on a keyless run, and a failure said as one."""
    if getattr(getattr(run.gen, "_config", None), "deterministic", False):
        return None, False
    material = await _overview_material(run, capabilities)
    return material, material is None


def _has_written_overview(gen: Any, repo_name: str) -> bool:
    prior = (getattr(gen, "_prior_pages", {}) or {}).get(compute_page_id("repo_overview", repo_name))
    return prior is not None and getattr(prior, "provider_name", "") != "template"


def flow_module_index(module_groups: list[Any]) -> dict[str, tuple[str, str]]:
    """``file -> (module page id, title)``: the page a flow step points at.

    A file's documenting page is the group that claims it in ``file_paths``,
    the same ownership rule the docs tree places file pages by.
    """
    index: dict[str, tuple[str, str]] = {}
    for mg in module_groups:
        page_id = compute_page_id("module_page", mg.key)
        for path in mg.file_paths:
            index.setdefault(path, (page_id, mg.display))
    return index


def build_flow_coros(run: _GenerationRun) -> list[tuple[str, Any]]:
    """Level 5 (flow_page): one walkthrough per selected operation."""
    plans = run.flow_plans()
    emitted = [plan for plan in plans if run._emit(plan.page_id)]
    if not emitted:
        return []
    modules = flow_module_index(run.sel_module_groups)
    return [
        (
            plan.page_id,
            run.gen.generate_flow_page(
                plan,
                graph=run.graph,
                modules=modules,
                source_map=run.source_map,
                repo_path=run.repo_path,
                related=[other.title for other in plans if other is not plan],
            ),
        )
        for plan in emitted
    ]


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

    signals = await _onboarding_signals(run, [spec for _page_id, spec in emitted])
    # Contexts are built here rather than per page, so each page knows which
    # page follows it: a slot whose gate fails is skipped in the reading order.
    contexts = {spec.slot: spec.build_context(signals) for _page_id, spec in emitted}
    present = _present_slots(gen, specs, contexts)
    for page_id, spec in emitted:
        nxt = _onboarding.next_slot(spec.slot, present)
        title = _onboarding.SLOT_TITLES[nxt] if nxt else None
        page = gen.generate_onboarding_page(
            spec, signals, ctx=contexts[spec.slot], next_title=title
        )
        coros.append((page_id, page))
    return coros


def _present_slots(gen: Any, specs: list[Any], contexts: dict[str, Any]) -> set[str]:
    """Slots that will have a page: built this run, or kept from an earlier one."""
    prior = getattr(gen, "_prior_pages", {}) or {}
    present = {slot for slot, ctx in contexts.items() if ctx is not None}
    present |= {
        spec.slot
        for spec in specs
        if spec.slot not in contexts
        and compute_page_id("onboarding", _onboarding.target_path(spec.slot)) in prior
    }
    return present


async def _onboarding_signals(run: _GenerationRun, specs: list[Any]) -> Any:
    """The shared inputs of the onboarding pages this run writes."""
    from .front_page import run_journeys, top_parts

    kg_layers: tuple[dict, ...] = ()
    kg_tour_steps: tuple[dict, ...] = ()
    if run.kg_ctx and run.kg_ctx.available:
        kg_layers = tuple(run.kg_ctx.get_layers())
        kg_tour_steps = tuple(run.kg_ctx.get_tour())

    # The corpus and the parts cost a batched store read, so only a run
    # emitting a subkind that declared it wants one pays for it. Memoised, so a
    # run that also emits the overview builds them once for both.
    module_corroboration = (
        tuple(await _module_corroboration(run))
        if any(spec.needs_module_corroboration for spec in specs)
        else ()
    )
    parts = (
        tuple(top_parts(run.sel_module_groups, await _module_summaries(run), run.source_map))
        if any(spec.needs_parts for spec in specs)
        else ()
    )

    return _onboarding.OnboardingSignals(
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
        doc_sources=_doc_sources(run),
        journeys=tuple(run_journeys(run)),
        parts=parts,
        surfaces=_surfaces(run),
    )
