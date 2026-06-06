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

from ...analysis.knowledge_graph import _slugify
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


async def _prefetch_rag_context(
    run: _GenerationRun, items: list[tuple[Any, FilePageContext]]
) -> bool:
    """Resolve RAG context for all tier-1 file pages in one batched search.

    Replicates the per-page gating in ``_generate_file_page_from_ctx`` (flag,
    store size, query-term derivation, self-exclusion) but runs BEFORE the
    level starts, outside the LLM semaphore, with all queries embedded in a
    single embedder call via :meth:`VectorStore.search_many`. The store is
    static for the whole level (pages embed in one batch at level end), so
    prefetched results are identical to what each page would have fetched.

    Returns True when the per-page search can be skipped (results resolved,
    or the per-page gate would have skipped every search anyway); False on
    batch failure so pages fall back to the per-page path.
    """
    if run.vector_store is None or not getattr(run.config, "enable_rag_context", True):
        return False
    min_store_size = max(0, int(getattr(run.config, "rag_min_store_size", 10) or 0))
    if min_store_size > 0:
        try:
            current_ids = await run.vector_store.list_page_ids()
            if len(current_ids) < min_store_size:
                # Store too small for useful RAG — the per-page gate would
                # skip every search this level, so there is nothing to fetch.
                return True
        except Exception:
            pass  # same as the per-page gate: fall through to the search
    queries: list[str] = []
    targets: list[tuple[Any, FilePageContext]] = []
    for p, ctx in items:
        query_terms = p.exports or [
            s["name"] for s in ctx.symbols[:3] if s.get("visibility") == "public"
        ]
        if not query_terms:
            continue
        queries.append(", ".join(query_terms[:5]))
        targets.append((p, ctx))
    if not queries:
        return True
    try:
        all_results = await run.vector_store.search_many(queries, limit=3)
    except Exception as exc:
        log.debug("rag.batch_search_failed", error=str(exc))
        return False
    for (p, ctx), results in zip(targets, all_results, strict=False):
        self_id = f"file_page:{p.file_info.path}"
        ctx.rag_context = [
            f"[{r.page_id}]\n{r.snippet}" for r in results if r.page_id != self_id
        ]
    return True


async def build_level2_coros(run: _GenerationRun) -> list[tuple[str, Any]]:
    """Level 2 (file_page): topo-ordered context assembly + tier routing.

    Context is assembled for ALL code files (module pages need it). Pages are
    emitted only for files in the selection allow-set. Tier-1 paths get the
    full LLM path; tier-2 paths get the deterministic template renderer.
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
            symbol_index=symbol_index,
        )
        run.file_page_contexts[p.file_info.path] = ctx
        items.append((p, ctx))

    def _emits_tier1(p: Any) -> bool:
        path = p.file_info.path
        return (
            path in run.sel_file_paths
            and path in run.tier1_paths
            and compute_page_id("file_page", path) not in run.completed_ids
        )

    rag_prefetched = await _prefetch_rag_context(
        run, [(p, ctx) for p, ctx in items if _emits_tier1(p)]
    )

    coros: list[tuple[str, Any]] = []
    for p, ctx in items:
        path = p.file_info.path
        pid = compute_page_id("file_page", path)
        if path in run.sel_file_paths and pid not in run.completed_ids:
            if path in run.tier1_paths:
                coros.append(
                    (pid, gen._generate_file_page_from_ctx(p, ctx, rag_prefetched=rag_prefetched))
                )
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


def build_level5_coros(run: _GenerationRun) -> list[tuple[str, Any]]:
    """Level 5 (layer_page): one page per KG layer with >= 3 files."""
    gen = run.gen
    coros: list[tuple[str, Any]] = []
    if not run.kg_ctx.available:
        return coros

    from ..context_assembler import LayerPageContext

    _MIN_LAYER_FILES = 3

    for layer in run.kg_ctx.get_layers():
        node_ids = layer.get("nodeIds", [])
        file_paths = [nid[5:] for nid in node_ids if nid.startswith("file:")]
        if len(file_paths) < _MIN_LAYER_FILES:
            continue

        layer_name = layer.get("name", "")
        # Key the page by the layer's STABLE slug id (``layer:<slug>``), not its
        # display name: the LLM layer-name enrichment rewrites ``name`` after
        # generation, so a name-keyed page would no longer join to its KG layer.
        # ``id`` is derived from the deterministic heuristic name at curation
        # time and never changes under enrichment.
        layer_id = layer.get("id", "") or f"layer:{_slugify(layer_name)}"
        page_id = compute_page_id("layer_page", layer_id)
        if page_id in run.completed_ids:
            continue

        key_files: list[dict] = []
        entry_points: list[str] = []
        edge_connectors: list[str] = []
        tour_steps_seen: set[int] = set()
        tour_steps: list[dict] = []

        ranked = sorted(
            file_paths,
            key=lambda p: run.pagerank.get(p, 0.0),
            reverse=True,
        )
        for fp in ranked[:10]:
            fc = run.kg_ctx.get_file_context(fp)
            entry: dict = {
                "path": fp,
                "role": fc.role if fc else "internal",
                "summary": (run.completed_page_summaries.get(fp) or "")[:200],
            }
            key_files.append(entry)
            if fc:
                if fc.role == "entry_point":
                    entry_points.append(fp)
                elif fc.role == "edge_connector":
                    edge_connectors.append(fp)
                if fc.tour_step and fc.tour_step["order"] not in tour_steps_seen:
                    tour_steps_seen.add(fc.tour_step["order"])
                    tour_steps.append(fc.tour_step)

        deps_out, deps_in = run.kg_ctx.get_inter_layer_edges(layer)
        tour_steps.sort(key=lambda s: s["order"])

        ctx = LayerPageContext(
            layer_name=layer_name,
            layer_id=layer_id,
            layer_description=layer.get("description", ""),
            file_count=len(file_paths),
            key_files=key_files,
            deps_out=deps_out,
            deps_in=deps_in,
            tour_steps=tour_steps,
            entry_points=entry_points,
            edge_connectors=edge_connectors,
        )
        coros.append((page_id, gen.generate_layer_page(ctx)))

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
    kg_layers: tuple[dict, ...] = ()
    kg_tour_steps: tuple[dict, ...] = ()
    if run.kg_ctx and run.kg_ctx.available:
        kg_layers = tuple(run.kg_ctx.get_layers())
        kg_tour_steps = tuple(run.kg_ctx.get_tour())

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
    )
    for spec in specs:
        page_id = compute_page_id("onboarding", _onboarding.target_path(spec.slot))
        if page_id in run.completed_ids:
            continue
        coros.append((page_id, gen.generate_onboarding_page(spec, signals)))
    return coros
