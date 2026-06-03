"""Curation/presentation pass over the deterministic KG skeleton.

The exported knowledge graph is a *presentation* artifact, distinct from the
AST/dependency graph that powers queries. This module is the single seam where
the skeleton produced by :func:`build_knowledge_graph_skeleton` is reshaped into
something a human (or an AI reading the graph cold) can navigate: bounded,
dependency-ordered layers; a capped, ranked set of real entry points; one
canonical layer-aware tour; typed infra/CI/data nodes; and never-empty
summaries.

**Hard invariant.** Curation reads the NetworkX graph, communities, and
centrality, but it *only ever writes the returned* :class:`KnowledgeGraphResult`.
It never mutates ``graph_builder``'s graph, ``graph_edges``, centrality caches,
community detection, or any DB table. There is a regression test that asserts the
graph's node/edge counts are identical before and after this pass.

Curation is feature-flagged (``REPOWISE_KG_CURATION``) and defaults **off** so
the exported KG is byte-identical to today's until the multi-repo validation
gate passes. With the flag off, :func:`curate_knowledge_graph` is a no-op that
returns its input unchanged.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any

from repowise.core.analysis.knowledge_graph import KnowledgeGraphResult, _slugify
from repowise.core.generation.layers import compute_layer_order, infer_layer
from repowise.core.generation.tour import (
    DEFAULT_MAX_STOPS,
    build_tour,
    score_entry_points,
)

__all__ = ["curate_knowledge_graph", "curation_enabled"]

logger = logging.getLogger(__name__)


_FLAG_ENV = "REPOWISE_KG_CURATION"

# A primary layer larger than this many files, or spanning more than this many
# distinct sub-directories, is given a two-level structure (primary → named
# sub-groups) so a mega-layer like core/* or ui/* stays drill-down legible
# instead of becoming one opaque bucket (plan §Phase 1, edge case B).
_SUBSPLIT_FILE_THRESHOLD = 60
_SUBSPLIT_DIR_THRESHOLD = 8

# Hard bound on the curated primary-layer count. The spine is bounded ≤~11 by
# construction; if a future change ever blows past this we degrade to the
# uncurated layers rather than ship an unreadable list.
_MAX_LAYERS = 15

# Entry-point precision (plan §Phase 2). A re-export *barrel* (typically an
# ``index.ts``) carries the ``index`` stem heuristic's ``entry_point`` flag but
# teaches a reader nothing, so it is demoted in the presentation view. Runtime
# entries that survive are ranked by ``pagerank + betweenness`` and the surfaced
# set is capped — the full ranked list is kept as ``entry_candidates``.
_BARREL_STEMS = frozenset({"index"})
_SUBSTANTIVE_KINDS = frozenset(
    {"function", "method", "class", "struct", "interface", "enum", "trait", "impl", "macro"}
)
_MAX_ENTRY_POINTS = 8


def curation_enabled() -> bool:
    """Whether KG curation is enabled via the ``REPOWISE_KG_CURATION`` env flag.

    Defaults to **off**. Any of ``1``/``true``/``yes``/``on`` (case-insensitive)
    turns it on. Resolved at the call site so :func:`curate_knowledge_graph`
    itself stays pure and trivially testable with an explicit ``enabled=``.
    """
    return os.environ.get(_FLAG_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def curate_knowledge_graph(
    kg: KnowledgeGraphResult,
    *,
    parsed_files: list[Any],
    graph_builder: Any,
    repo_structure: Any,
    community_info: Any,
    enabled: bool = False,
) -> KnowledgeGraphResult:
    """Reshape the KG skeleton into an intuitive presentation artifact.

    Pure with respect to the AST graph: reads ``graph_builder`` /
    ``community_info`` but writes only the returned result. When ``enabled`` is
    ``False`` this is a strict no-op returning ``kg`` unchanged (the default, so
    the exported KG is unaffected until the flag flips).

    Each curation step is added in a later phase and guarded so that a failure
    degrades to the prior (uncurated) field rather than aborting the export.
    """
    if not enabled:
        return kg

    # Each step mutates only ``kg`` (the presentation result) and is guarded so
    # a failure degrades to the prior, uncurated field rather than aborting the
    # export. Steps are layered in by subsequent phases:
    #   _curate_layers -> _curate_entry_points -> _curate_tour
    #   -> _curate_node_types -> _curate_summaries
    try:
        curated = _curate_layers(kg, graph_builder)
        if curated is not None:
            kg.layers = curated
    except Exception:  # pragma: no cover - defensive; keep uncurated layers
        logger.exception("kg_curation._curate_layers failed; keeping community layers")

    try:
        _curate_entry_points(kg, parsed_files, graph_builder)
    except Exception:  # pragma: no cover - defensive; keep skeleton entry points
        logger.exception("kg_curation._curate_entry_points failed; keeping raw entry points")

    try:
        tour = _curate_tour(kg, parsed_files, graph_builder)
        if tour is not None:
            kg.tour = tour
    except Exception:  # pragma: no cover - defensive; keep skeleton/LLM tour
        logger.exception("kg_curation._curate_tour failed; keeping existing tour")

    return kg


# ---------------------------------------------------------------------------
# Phase 1 — curated layers (replace raw-community layers with the spine)
# ---------------------------------------------------------------------------


def _file_nodes(kg: KnowledgeGraphResult) -> list[dict]:
    """Return the file-typed nodes of *kg* (ids prefixed ``file:``)."""
    return [
        n
        for n in kg.nodes
        if isinstance(n.get("id"), str)
        and n["id"].startswith("file:")
        and isinstance(n.get("filePath"), str)
    ]


def _file_import_edges(graph_builder: Any) -> list[tuple[str, str]]:
    """``(src, dst)`` string edges from the AST graph (src imports dst).

    Mirrors the wiki spine's edge extraction. Symbol-node ids and externals are
    naturally ignored downstream by :func:`compute_layer_order`, which only
    counts edges whose endpoints are both in ``file_layers``.
    """
    edges: list[tuple[str, str]] = []
    try:
        for src, dst in graph_builder.graph().edges():
            if isinstance(src, str) and isinstance(dst, str):
                edges.append((src, dst))
    except Exception:  # pragma: no cover - defensive
        pass
    return edges


def _common_dir_prefix(seg_lists: list[tuple[str, ...]]) -> tuple[str, ...]:
    """Longest common leading directory-segment prefix across *seg_lists*."""
    if not seg_lists:
        return ()
    common = list(seg_lists[0])
    for segs in seg_lists[1:]:
        i = 0
        while i < len(common) and i < len(segs) and common[i] == segs[i]:
            i += 1
        del common[i:]
        if not common:
            break
    return tuple(common)


def _sub_split(layer_id: str, node_ids: list[str], id_to_path: dict[str, str]) -> list[dict] | None:
    """Two-level sub-groups for an oversized/wide primary layer, else ``None``.

    Groups files by the first path segment that distinguishes them (the segment
    after the layer's common directory prefix), so e.g. ``core/ingestion`` /
    ``core/analysis`` / ``core/generation`` become named sub-groups. Only kicks
    in past the size/width thresholds and only when it yields ≥2 groups.
    """
    if len(node_ids) < 2:
        return None

    dir_segs = {nid: PurePosixPath(id_to_path[nid]).parts[:-1] for nid in node_ids}
    common = _common_dir_prefix(list(dir_segs.values()))

    groups: dict[str, list[str]] = defaultdict(list)
    for nid in node_ids:
        segs = dir_segs[nid]
        key = segs[len(common)] if len(segs) > len(common) else "(root)"
        groups[key].append(nid)

    oversized = len(node_ids) > _SUBSPLIT_FILE_THRESHOLD
    wide = len(groups) > _SUBSPLIT_DIR_THRESHOLD
    if not (oversized or wide) or len(groups) < 2:
        return None

    return [
        {"id": f"{layer_id}:{_slugify(name)}", "name": name, "nodeIds": groups[name]}
        for name in sorted(groups)
    ]


def _curate_layers(kg: KnowledgeGraphResult, graph_builder: Any) -> list[dict] | None:
    """Build bounded, dependency-ordered layers from the ``infer_layer`` spine.

    Returns the curated layer list, or ``None`` to keep the existing
    (community) layers when the result would be degenerate or violate the
    partition / bound invariants. Every file lands in exactly one layer, so the
    partition (Σ nodeIds == file-node count) and singleton-elimination hold by
    construction.
    """
    file_nodes = _file_nodes(kg)
    if not file_nodes:
        return None

    id_to_path = {n["id"]: n["filePath"] for n in file_nodes}
    file_layers = {n["filePath"]: infer_layer(n["filePath"]) for n in file_nodes}
    order = compute_layer_order(file_layers, _file_import_edges(graph_builder))

    by_layer: dict[str, list[str]] = defaultdict(list)
    for n in file_nodes:
        by_layer[file_layers[n["filePath"]]].append(n["id"])

    layers: list[dict] = []
    for display_order, layer_name in enumerate(order):
        node_ids = by_layer[layer_name]
        layer_id = f"layer:{_slugify(layer_name)}"
        layer: dict[str, Any] = {
            "id": layer_id,
            "name": layer_name,
            "description": "",
            "nodeIds": node_ids,
            "display_order": display_order,
        }
        sub_groups = _sub_split(layer_id, node_ids, id_to_path)
        if sub_groups:
            layer["subGroups"] = sub_groups
        layers.append(layer)

    # Degrade rather than ship a broken artifact: enforce bound + partition.
    total = sum(len(layer["nodeIds"]) for layer in layers)
    if not layers or len(layers) > _MAX_LAYERS or total != len(file_nodes):
        logger.warning(
            "kg_curation: curated layers failed invariant "
            "(count=%d, partition=%d/%d); keeping community layers",
            len(layers),
            total,
            len(file_nodes),
        )
        return None
    return layers


# ---------------------------------------------------------------------------
# Phase 2 — entry-point precision (demote barrels, rank + cap survivors)
# ---------------------------------------------------------------------------


def _is_barrel(parsed_file: Any) -> bool:
    """True if *parsed_file* is a re-export barrel (``index`` shell, no runtime).

    Conservative by design: a file is a barrel only when its stem is ``index``
    and it defines no runtime-bearing symbol (function/class/method/…) — purely
    re-exporting or empty. Anything that defines executable behaviour, even if
    named ``index``, is kept as a genuine entry candidate.
    """
    fi = getattr(parsed_file, "file_info", None)
    path = getattr(fi, "path", "")
    if PurePosixPath(path).stem.lower() not in _BARREL_STEMS:
        return False

    symbols = getattr(parsed_file, "symbols", []) or []
    if any(getattr(s, "kind", "") in _SUBSTANTIVE_KINDS for s in symbols):
        return False

    has_reexports = any(
        getattr(imp, "is_reexport", False) for imp in getattr(parsed_file, "imports", []) or []
    )
    exports_only = bool(getattr(parsed_file, "exports", []))
    return has_reexports or exports_only or not symbols


def _curate_entry_points(
    kg: KnowledgeGraphResult, parsed_files: list[Any], graph_builder: Any
) -> None:
    """Demote re-export barrels and surface a capped, ranked entry-point set.

    Mutates only the presentation view: drops the ``entry_point`` *tag* from
    barrel nodes (and adds a ``barrel`` tag) without touching the AST graph's
    ``is_entry_point`` flag (the dead-code pass relies on it). Survivors are
    ranked by ``pagerank + betweenness``; ``project.entry_points`` holds the top
    few, ``project.entry_candidates`` the full ranked list.
    """
    pf_by_path = {pf.file_info.path: pf for pf in parsed_files if getattr(pf, "file_info", None)}
    pagerank = graph_builder.pagerank() or {}
    try:
        betweenness = graph_builder.betweenness_centrality() or {}
    except Exception:  # pragma: no cover - defensive
        betweenness = {}

    survivors: list[tuple[float, str]] = []
    for node in kg.nodes:
        nid = node.get("id", "")
        if not (isinstance(nid, str) and nid.startswith("file:")):
            continue
        tags = node.get("tags") or []
        if "entry_point" not in tags:
            continue
        path = node.get("filePath", "")
        pf = pf_by_path.get(path)
        if pf is not None and _is_barrel(pf):
            new_tags = [t for t in tags if t != "entry_point"]
            if "barrel" not in new_tags:
                new_tags.append("barrel")
            node["tags"] = new_tags
            continue
        score = pagerank.get(path, 0.0) + betweenness.get(path, 0.0)
        survivors.append((score, path))

    # Highest score first; path as a stable, deterministic tie-break.
    survivors.sort(key=lambda sp: (-sp[0], sp[1]))
    ranked = [path for _, path in survivors]
    kg.project["entry_points"] = ranked[:_MAX_ENTRY_POINTS]
    kg.project["entry_candidates"] = ranked


# ---------------------------------------------------------------------------
# Phase 3 — canonical, layer-aware tour
# ---------------------------------------------------------------------------


def _readme_overview_node(kg: KnowledgeGraphResult) -> dict | None:
    """The best root-level README/overview file node, if one exists."""
    best: dict | None = None
    for n in _file_nodes(kg):
        path = n["filePath"]
        name = PurePosixPath(path).name.lower()
        depth = len(PurePosixPath(path).parts) - 1
        if not (name.startswith("readme") and depth <= 1):
            continue
        # Prefer the shallowest README (the repo-root one).
        if best is None or depth < (len(PurePosixPath(best["filePath"]).parts) - 1):
            best = n
    return best


def _best_in_layer(paths: list[str], rank: dict[str, float], pagerank: dict[str, float]) -> str:
    """Highest-ranked path in a layer (entry score, then PageRank, then name)."""
    return sorted(paths, key=lambda p: (-rank.get(p, 0.0), -pagerank.get(p, 0.0), p))[0]


def _curate_tour(
    kg: KnowledgeGraphResult, parsed_files: list[Any], graph_builder: Any
) -> list[dict] | None:
    """Build one canonical, layer-aware tour over the curated layers.

    Uses the deterministic :func:`build_tour` (BFS-from-entry + PageRank) as the
    base ordering, opens with the repo README/overview, then diversifies so the
    walk covers as many curated layers as the step budget allows (swapping
    redundant same-layer stops for representatives of uncovered layers). Every
    step carries a ``layer_id`` mapping it to a curated layer, so the tour reads
    the architecture top→bottom. The LLM may later rewrite step *prose* only.
    """
    file_nodes = _file_nodes(kg)
    if not file_nodes:
        return None

    paths = [n["filePath"] for n in file_nodes]
    type_by_path = {n["filePath"]: n.get("type", "file") for n in file_nodes}
    file_layers = {p: infer_layer(p) for p in paths}
    order = compute_layer_order(file_layers, _file_import_edges(graph_builder))
    layer_index = {name: i for i, name in enumerate(order)}

    pagerank = graph_builder.pagerank() or {}
    rank = {path: s for s, path in score_entry_points(parsed_files, pagerank)}

    # Infra files (Docker/CI/etc.) close the tour; everything else is code.
    infra_paths = [p for p in paths if type_by_path.get(p) in {"service", "pipeline"}]

    project_name = kg.project.get("name") or "repository"
    base = build_tour(
        parsed_files,
        pagerank,
        _file_import_edges(graph_builder),
        file_page_paths=paths,
        infra_paths=infra_paths,
        repo_name=project_name,
        max_stops=DEFAULT_MAX_STOPS,
    )

    overview = [s for s in base if s.kind == "overview"]
    code = [s for s in base if s.kind == "code"]
    infra = [s for s in base if s.kind == "infra"]

    # --- Diversify code stops for layer coverage -------------------------
    by_layer: dict[str, list[str]] = defaultdict(list)
    for p in paths:
        by_layer[file_layers[p]].append(p)

    code_paths = [s.target_path for s in code]
    seen_layers: set[str] = set()
    redundant_positions: list[int] = []
    for i, p in enumerate(code_paths):
        layer = file_layers.get(p)
        if layer in seen_layers:
            redundant_positions.append(i)
        else:
            seen_layers.add(layer)

    uncovered = [name for name in order if name not in seen_layers]
    for layer in uncovered:
        if not redundant_positions:
            break
        candidates = [p for p in by_layer.get(layer, []) if p not in code_paths]
        if not candidates:
            continue
        rep = _best_in_layer(candidates, rank, pagerank)
        pos = redundant_positions.pop()
        code_paths[pos] = rep
        seen_layers.add(layer)

    # Order the walk top→bottom: by layer dependency rank, then path.
    code_paths = sorted(
        dict.fromkeys(code_paths),
        key=lambda p: (layer_index.get(file_layers.get(p, ""), len(order)), p),
    )

    # --- Assemble the exported tour --------------------------------------
    tour: list[dict] = []
    order_n = 0

    readme = _readme_overview_node(kg)
    if overview:
        order_n += 1
        ov = overview[0].as_dict()
        ov["order"] = order_n
        if readme is not None:
            ov["target_path"] = readme["filePath"]
            ov["title"] = PurePosixPath(readme["filePath"]).name
            ov["layer_id"] = f"layer:{_slugify(file_layers[readme['filePath']])}"
        else:
            ov["layer_id"] = None
        tour.append(ov)

    for p in code_paths:
        order_n += 1
        layer = file_layers.get(p, "")
        idx = layer_index.get(layer, len(order))
        if idx == 0:
            reason = f"Top of the stack ({layer}) — start of the request/control flow."
        elif idx >= len(order) - 1:
            reason = f"Foundational layer ({layer}) — the others build on this."
        else:
            reason = f"The {layer} layer — sits mid-stack between consumers and foundations."
        tour.append(
            {
                "order": order_n,
                "target_path": p,
                "page_type": "file_page",
                "title": PurePosixPath(p).name,
                "depth": idx,
                "kind": "code",
                "reason": reason,
                "layer_id": f"layer:{_slugify(layer)}",
            }
        )

    for s in infra:
        order_n += 1
        step = s.as_dict()
        step["order"] = order_n
        step["layer_id"] = f"layer:{_slugify(file_layers.get(s.target_path, 'Config'))}"
        tour.append(step)

    return tour
