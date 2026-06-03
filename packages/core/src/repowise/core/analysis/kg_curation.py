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
