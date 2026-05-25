"""Per-level coroutine builders for the generation orchestrator.

Each function takes the live :class:`_GenerationRun` and returns a list of
``(page_id, coroutine)`` tuples for one generation level. They read graph
metrics, selection allow-sets, and the shared context cache off the run
object. Behaviour mirrors the original inline ``generate_all`` exactly.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

import structlog

from .. import onboarding as _onboarding
from ..context_assembler import FilePageContext
from ..models import compute_page_id
from .helpers import _is_infra_file

if TYPE_CHECKING:
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
        if compute_page_id("api_contract", p.file_info.path) not in run.completed_ids
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
        if compute_page_id("symbol_spotlight", f"{pf.file_info.path}::{sym.name}")
        not in run.completed_ids
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
            if dep.startswith("external:"):
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
    """Level 2 (file_page): topo-ordered context assembly + tier routing.

    Context is assembled for ALL code files (module pages need it). Pages are
    emitted only for files in the selection allow-set. Tier-1 paths get the
    full LLM path; tier-2 paths get the deterministic template renderer.
    """
    gen = run.gen
    _topo_order_code_files(run)
    await _prefetch_dependency_summaries(run)

    coros: list[tuple[str, Any]] = []
    for p in run.code_files:
        kg_file_ctx = run.kg_ctx.get_file_context(p.file_info.path) if run.kg_ctx.available else None
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
        )
        run.file_page_contexts[p.file_info.path] = ctx
        path = p.file_info.path
        pid = compute_page_id("file_page", path)
        if path in run.sel_file_paths and pid not in run.completed_ids:
            if path in run.tier1_paths:
                coros.append((pid, gen._generate_file_page_from_ctx(p, ctx)))
            else:
                coros.append((pid, gen._generate_file_page_tier2(p, ctx)))
    return coros


def build_level3_coros(run: _GenerationRun) -> list[tuple[str, Any]]:
    """Level 3 (scc_page), allow-set filtered."""
    gen = run.gen
    coros: list[tuple[str, Any]] = []
    for scc_id, scc_files in run.sel_scc_groups:
        fc_list = [
            run.file_page_contexts[f] for f in scc_files if f in run.file_page_contexts
        ]
        pid = compute_page_id("scc_page", scc_id)
        if pid not in run.completed_ids:
            coros.append((pid, gen.generate_scc_page(scc_id, scc_files, fc_list)))
    return coros


def build_level4_coros(run: _GenerationRun) -> list[tuple[str, Any]]:
    """Level 4 (module_page), allow-set filtered."""
    gen = run.gen
    coros: list[tuple[str, Any]] = []
    for mg in run.sel_module_groups:
        fcs = [
            run.file_page_contexts[fp]
            for fp in mg.file_paths
            if fp in run.file_page_contexts
        ]
        if not fcs:
            continue
        page_id = compute_page_id("module_page", mg.key)
        if page_id in run.completed_ids:
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
                    decision_records=run.decisions_all,
                    dead_code_findings=[
                        d for fc in fcs for d in run.dead_code_by_file.get(fc.file_path, [])
                    ],
                    external_systems=run.external_systems,
                    community_label=mg.label,
                    community_cohesion=mg.cohesion,
                    target_path=mg.key,
                ),
            )
        )
    return coros


def build_level6_coros(run: _GenerationRun) -> list[tuple[str, Any]]:
    """Level 6 (repo_overview + architecture_diagram)."""
    gen = run.gen
    coros: list[tuple[str, Any]] = []
    if compute_page_id("repo_overview", run.repo_name) not in run.completed_ids:
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
                    decision_records=run.decisions_all[:10],
                ),
            )
        )
    if compute_page_id("architecture_diagram", run.repo_name) not in run.completed_ids:
        coros.append(
            (
                compute_page_id("architecture_diagram", run.repo_name),
                gen.generate_architecture_diagram(
                    run.graph, run.pagerank, run.community, run.sccs, run.repo_name
                ),
            )
        )
    return coros


def build_level7_coros(run: _GenerationRun) -> list[tuple[str, Any]]:
    """Level 7 (infra_page), allow-set filtered."""
    gen = run.gen
    infra_files = [
        p
        for p in run.parsed_files
        if _is_infra_file(p) and p.file_info.path in run.sel_infra_paths
    ]
    return [
        (
            compute_page_id("infra_page", p.file_info.path),
            gen.generate_infra_page(p, run.source_map.get(p.file_info.path, b"")),
        )
        for p in infra_files
        if compute_page_id("infra_page", p.file_info.path) not in run.completed_ids
    ]


def build_level8_coros(run: _GenerationRun) -> list[tuple[str, Any]]:
    """Level 8 (curated onboarding collection)."""
    gen = run.gen
    coros: list[tuple[str, Any]] = []
    if not getattr(run.config, "enable_onboarding", True):
        return coros
    specs = _onboarding.iter_specs()
    if not specs:
        return coros
    if run.on_subphase is not None:
        with contextlib.suppress(Exception):
            run.on_subphase("onboarding", len(specs))
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
    )
    for spec in specs:
        page_id = compute_page_id("onboarding", _onboarding.target_path(spec.slot))
        if page_id in run.completed_ids:
            continue
        coros.append((page_id, gen.generate_onboarding_page(spec, signals)))
    return coros
