"""Zoom-map builder service.

Public API
----------
``build_zoom_map(session, repo, *, max_depth=None, focus=None)`` returns the
nested containment tree (system -> layer -> group -> folder -> file) with
execution-aware importance, rolled-up metrics and parent-relative relations.
Placement is the canvas's job, not this service's.

Like :mod:`c4_builder`, the map is derived ON DEMAND from the persisted graph
(it reuses ``c4_builder.build_architecture_view`` for the heavy load), so it
works on hosted backends with no checkout and never goes stale. Everything below
the load is pure functions (tree / scoring / metrics / relations) that
unit-test without a session.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.external_systems.links import IMPORT_TARGET_LANGUAGES
from repowise.server.services.c4_builder.architecture import build_architecture_view
from repowise.server.services.c4_builder.models import ArchitectureView

from .calls import load_projected_calls
from .containers import build_containers
from .externals import build_externals, resolve_imports
from .metrics import rollup_health, rollup_metrics
from .models import ZoomContainer, ZoomExternal, ZoomMap, ZoomNode, ZoomRelation, evolve
from .relations import aggregate_relations
from .scoring import FileStat, compute_file_signals, score_tree
from .technology import TECHNOLOGY_LIMIT, file_technologies, rollup_labels
from .tree import GroupSpec, LayerSpec, LeafInfo, ModuleInfo, build_tree

__all__ = [
    # ``ModuleInfo`` is part of the public surface because the hosted builder
    # calls ``assemble_zoom_map`` directly rather than porting it.
    "ModuleInfo",
    "ZoomContainer",
    "ZoomExternal",
    "ZoomMap",
    "ZoomNode",
    "ZoomRelation",
    "assemble_zoom_map",
    "build_zoom_map",
]


def _tour_paths(view: ArchitectureView) -> set[str]:
    paths: set[str] = set()
    for step in view.tour:
        if step.target_path:
            paths.add(step.target_path)
        paths.update(step.node_ids)
    return paths


def assemble_zoom_map(
    view: ArchitectureView,
    *,
    max_depth: int | None = None,
    focus: str | None = None,
    health: dict[str, tuple[float, int]] | None = None,
    modules: dict[str, ModuleInfo] | None = None,
    call_edges: list[tuple[str, str, str]] | None = None,
    checkout: str | None = None,
) -> ZoomMap:
    """Pure assembly: ``ArchitectureView`` -> ``ZoomMap``. No DB.

    ``health`` maps a file path to ``(score, loc)`` where ``score`` is the 0..10
    code-health score (higher = healthier) and ``loc`` is the rollup weight. It is
    optional so the pure assembly still unit-tests without health data; files not
    present in the map read as unscored (neutral), exactly like the treemap.

    ``modules`` maps a directory path to the module page documenting it, so a
    folder card reads as the subsystem the docs name rather than as a path
    segment. Also optional: a repository indexed without a wiki has none, and
    the hosted builder does not have them in its artifacts yet.

    ``call_edges`` are symbol-level execution edges already projected onto file
    pairs by :mod:`.calls`. The view cannot carry them -- it is loaded file-only,
    so both endpoints of a symbol edge are filtered out before it arrives -- so
    they come in beside it. Optional for the same reasons as the two above.

    External systems, containers and technologies need nothing beyond the view
    (its ``external_systems``, its ``external:*`` import edges and its file
    paths), so a view without them yields empty lists. ``checkout`` is the
    repository on disk, read only to name a container after its manifest.
    """
    health = health or {}
    # The view is loaded file-only, but the curated node_type can be
    # config/document/service rather than "file"; a file node is reliably the
    # one whose id equals its own path and carries no symbol line range.
    file_nodes = [n for n in view.nodes if n.line_range is None and n.id == n.file_path]

    # Use the curated, cleaned entry-point list (Phase-1 ranking) rather than the
    # raw ingestion ``is_entry_point`` flag. Candidacy runs at ingestion, so
    # the flag does not tag configs (server.json) or leaf resolvers
    # (dotnet/index.py); what the curated set adds on top is the
    # adjacent-layer and support-path exclusions, which need a layer map, and
    # barrel demotion, which needs parsed files. Driving both the execution
    # bonus and the displayed leaf flag off it keeps the "how it runs" signal
    # honest.
    curated_entries = set(view.entry_points)

    file_stats = [
        FileStat(
            path=n.id,
            pagerank_pct=n.pagerank_percentile,
            betweenness=n.betweenness,
            degree=n.in_degree + n.out_degree,
            complexity=n.complexity,
            is_entry_point=n.id in curated_entries,
            is_test=n.is_test,
            is_hotspot=n.is_hotspot,
            is_dead=n.is_dead,
        )
        for n in file_nodes
    ]
    edges = [(e.source, e.target, e.edge_type) for e in view.edges]
    edges.extend(call_edges or ())

    signals = compute_file_signals(
        file_stats,
        edges,
        entry_points=list(view.entry_points),
        tour_paths=_tour_paths(view),
    )

    leaf_info: dict[str, LeafInfo] = {}
    for n in file_nodes:
        sig = signals.get(n.id)
        hscore, hloc = health.get(n.id, (None, 1))
        leaf_info[n.id] = LeafInfo(
            summary=n.summary,
            language=n.language,
            is_entry_point=n.id in curated_entries,
            is_hotspot=n.is_hotspot,
            is_dead=n.is_dead,
            is_test=n.is_test,
            on_flow=bool(sig and sig.on_flow),
            health_score=hscore,
            loc=hloc,
        )

    layers = [
        LayerSpec(
            id=layer.id,
            name=layer.name,
            description=layer.description,
            display_order=layer.display_order,
            node_ids=list(layer.node_ids),
            sub_groups=[
                GroupSpec(id=sg.id, name=sg.name, node_ids=list(sg.node_ids))
                for sg in layer.sub_groups
            ],
        )
        for layer in view.layers
    ]

    root_id, nodes = build_tree(view.project_name, layers, leaf_info, modules)
    nodes = rollup_metrics(root_id, nodes)
    nodes = rollup_health(root_id, nodes)
    nodes = score_tree(root_id, nodes, signals)
    relations = aggregate_relations(nodes, edges)
    nodes, boundary = _annotate_boundaries(view, root_id, nodes, edges, checkout)

    # Honest count of files actually placed in the tree (a file the view knows
    # about but that curation assigned to no layer is not in the tree, so
    # counting file_stats would over-report). Taken before depth/focus pruning.
    total_files = sum(1 for n in nodes.values() if n.kind == "file")
    # ...and the rest of that denominator, so the map can say how much it omits.
    unclaimed_files = max(len(file_stats) - total_files, 0)

    root_id, nodes, relations, truncated = _prune(
        root_id, nodes, relations, max_depth=max_depth, focus=focus
    )
    boundary = _prune_boundaries(boundary, nodes)

    present_depth = max((n.level for n in nodes.values()), default=0)
    return ZoomMap(
        root_id=root_id,
        nodes=nodes,
        relations=relations,
        project_name=view.project_name,
        total_files=total_files,
        max_depth=present_depth,
        truncated=truncated,
        unclaimed_files=unclaimed_files,
        externals=boundary.externals,
        external_relations=boundary.relations,
        containers=boundary.containers,
    )


@dataclass(frozen=True)
class _Boundaries:
    externals: tuple[ZoomExternal, ...] = ()
    relations: tuple[ZoomRelation, ...] = ()
    containers: tuple[ZoomContainer, ...] = ()


def _annotate_boundaries(
    view: ArchitectureView,
    root_id: str,
    nodes: dict[str, ZoomNode],
    edges: list[tuple[str, str, str]],
    checkout: str | None,
) -> tuple[dict[str, ZoomNode], _Boundaries]:
    """Fill technologies and container ids on the nodes; build externals and containers."""
    leaves = {n.path: n for n in nodes.values() if n.kind == "file"}
    imports = resolve_imports(edges, leaves, view.external_systems)
    externals, ext_relations = build_externals(nodes, imports)
    file_techs = file_technologies(nodes, imports)
    containers, owner = build_containers(
        # Every indexed file, so a manifest no layer claims still marks its root.
        (
            n.id
            for n in view.nodes
            if n.line_range is None
            and n.id == n.file_path
            and n.language not in IMPORT_TARGET_LANGUAGES
        ),
        leaves,
        labels=file_techs,
        imports=imports,
        edges=edges,
        entry_points=view.entry_points,
        project_name=view.project_name,
        checkout=checkout,
    )
    file_containers = {leaves[path].id: (cid,) for path, cid in owner.items()}
    techs = rollup_labels(root_id, nodes, file_techs, TECHNOLOGY_LIMIT)
    spans = rollup_labels(root_id, nodes, file_containers)
    annotated = {
        nid: evolve(
            n,
            technologies=techs.get(nid, ()),
            container_ids=spans.get(nid) or file_containers.get(nid, ()),
        )
        for nid, n in nodes.items()
    }
    return annotated, _Boundaries(externals, ext_relations, containers)


def _prune_boundaries(boundary: _Boundaries, nodes: dict[str, ZoomNode]) -> _Boundaries:
    """Keep the external links and the cards a pruned tree still reaches."""
    relations = tuple(
        r for r in boundary.relations if r.parent_id in nodes and r.source_id in nodes
    )
    reached = {r.target_id for r in relations}
    spanned = {cid for n in nodes.values() for cid in n.container_ids}
    return _Boundaries(
        externals=tuple(e for e in boundary.externals if e.id in reached),
        relations=relations,
        containers=tuple(c for c in boundary.containers if c.id in spanned),
    )


def _prune(
    root_id: str,
    nodes: dict[str, ZoomNode],
    relations: tuple[ZoomRelation, ...],
    *,
    max_depth: int | None,
    focus: str | None,
) -> tuple[str, dict[str, ZoomNode], tuple[ZoomRelation, ...], bool]:
    """Scope to ``focus`` subtree and/or cap to ``max_depth`` levels below it.

    Returns the (possibly new) root, the kept nodes, kept relations, and whether
    anything was dropped (so the renderer knows it can lazy-fetch deeper).
    """
    if focus is not None and focus not in nodes:
        # Unknown focus: serve an empty map rooted at the original system node.
        focus = None

    new_root = focus or root_id
    base_level = nodes[new_root].level
    keep: dict[str, ZoomNode] = {}
    truncated = False

    # Walk the subtree from the new root (order is irrelevant — depth is gated on
    # absolute level, not hop count — so a simple stack is fine).
    stack = [new_root]
    while stack:
        nid = stack.pop()
        node = nodes[nid]
        depth_from_root = node.level - base_level
        if max_depth is not None and depth_from_root >= max_depth:
            # Keep this node but drop its children (frontier of the served map).
            if node.children:
                truncated = True
                node = _without_children(node)
            keep[nid] = node
            continue
        keep[nid] = node
        stack.extend(node.children)

    # Re-root: the focus node becomes a parentless root in the served map.
    if new_root != root_id:
        keep[new_root] = _as_root(keep[new_root])

    kept_ids = set(keep)
    rels = tuple(
        r
        for r in relations
        if r.parent_id in kept_ids and r.source_id in kept_ids and r.target_id in kept_ids
    )
    return new_root, keep, rels, truncated


def _without_children(node: ZoomNode) -> ZoomNode:
    return replace(node, children=())


def _as_root(node: ZoomNode) -> ZoomNode:
    return replace(node, parent_id=None)


async def build_zoom_map(
    session: AsyncSession,
    repo_id: str,
    *,
    max_depth: int | None = None,
    focus: str | None = None,
) -> ZoomMap:
    """Derive the zoom map for ``repo_id`` from the persisted graph."""
    from repowise.core.persistence import crud
    from repowise.server.services.c4_builder import load_repo

    view = await build_architecture_view(session, repo_id, include_symbols=False)
    repo = await load_repo(session, repo_id)
    modules = await _load_module_names(session, repo_id)
    # The view is file-only, so the whole symbol-level execution graph was
    # filtered out of it. Read it back and project it onto file pairs.
    call_edges = await load_projected_calls(
        session,
        repo_id,
        # Same file-node test the assembly uses, so a projected edge can only
        # land on a file the tree will actually contain.
        {n.id for n in view.nodes if n.line_range is None and n.id == n.file_path},
    )
    # One extra read: per-file health, keyed by path -> (effective score, loc).
    # Effective score prefers the split ``defect_score`` and falls back to the
    # overall ``score``, exactly like GET /api/repos/{id}/files, so the zoom card
    # and the /files treemap color off the same number. loc (>=1) is the rollup
    # weight for container means.
    metrics = await crud.get_health_metrics(session, repo_id)
    health = {
        m.file_path: (
            m.defect_score if m.defect_score is not None else m.score,
            max(m.nloc or 1, 1),
        )
        for m in metrics
    }
    return assemble_zoom_map(
        view,
        max_depth=max_depth,
        focus=focus,
        health=health,
        modules=modules,
        call_edges=call_edges,
        checkout=repo.local_path if repo else None,
    )


async def _load_module_names(session: AsyncSession, repo_id: str) -> dict[str, ModuleInfo]:
    """Directory path -> the module page documenting it.

    A module page's ``target_path`` is the directory it covers, which is the
    same key the folder trie is built on, so this is a join and not a second
    clustering. Tombstoned pages are excluded for the same reason the docs
    reader excludes them: the page is gone, so its name is not the repository's
    name for that directory any more.
    """
    from sqlalchemy import select

    from repowise.core.persistence.models import Page

    rows = await session.execute(
        select(Page.target_path, Page.title, Page.summary, Page.id).where(
            Page.repository_id == repo_id,
            Page.page_type == "module_page",
            Page.freshness_status != "tombstone",
            Page.target_path.isnot(None),
        )
    )
    modules: dict[str, ModuleInfo] = {}
    for target_path, title, summary, page_id in rows:
        # A module whose target is a clustering ordinal rather than a real
        # directory has nothing to join to; it simply never matches a folder.
        if target_path and title:
            modules[target_path] = ModuleInfo(
                title=title, summary=summary or "", page_id=page_id
            )
    return modules
