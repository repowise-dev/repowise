"""Curation/presentation pass over the deterministic KG skeleton.

The exported knowledge graph is a *presentation* artifact, distinct from the
AST/dependency graph that powers queries. This module is the single seam where
the skeleton produced by :func:`build_knowledge_graph_skeleton` is reshaped into
something a human (or an AI reading the graph cold) can navigate: bounded,
dependency-ordered layers; a capped, ranked set of real entry points; one
canonical execution-flow tour; typed infra/CI/data nodes; and never-empty
summaries.

**Hard invariant.** Curation reads the NetworkX graph, communities, and
centrality, but it *only ever writes the returned* :class:`KnowledgeGraphResult`.
It never mutates ``graph_builder``'s graph, ``graph_edges``, centrality caches,
community detection, or any DB table. There is a regression test that asserts the
graph's node/edge counts are identical before and after this pass.

Curation is feature-flagged (``REPOWISE_KG_CURATION``) and defaults **on**;
the 38-repo cross-language validation matrix is the acceptance gate that
flipped it. Setting the flag to ``0``/``false``/``no``/``off`` makes
:func:`curate_knowledge_graph` a no-op that returns its input unchanged
(the raw uncurated export).
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from repowise.core.analysis.kg_inputs import (
    _SUBSTANTIVE_KINDS,
    _dominant_language,  # noqa: F401  (re-exported; tests import it from here)
    _file_import_edges,
    _file_nodes,
    _is_barrel,
)
from repowise.core.analysis.kg_tour import (  # noqa: F401  (re-exported)
    _anchor_fanout_rank,
    _curate_tour,
    _import_pairs_excluding_fanout,
)
from repowise.core.analysis.knowledge_graph import KnowledgeGraphResult, _slugify
from repowise.core.entry_candidacy import (
    conventional_entry_stems,
    not_an_execution_start,
)
from repowise.core.generation.entry_points import rank_entry_points
from repowise.core.generation.layers import (
    ADJACENT_LAYERS,
    compute_layer_order,
    infer_layer,
    layer_order_basis,
)
from repowise.core.generation.tour import (
    DEFAULT_MAX_STOPS,
    score_entry_points,
)
from repowise.core.generation.well_known_files import well_known_role
from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY
from repowise.core.support_paths import is_support_path

__all__ = [
    "KGValidation",
    "apply_summary_floor",
    "build_portable_kg",
    "curate_knowledge_graph",
    "curation_enabled",
    "derive_modules",
    "validate_kg",
]

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

# Entry points surfaced in ``project.entry_points``; the full ranked list is
# kept as ``entry_candidates``.
_MAX_ENTRY_POINTS = 8


def curation_enabled() -> bool:
    """Whether KG curation is enabled via the ``REPOWISE_KG_CURATION`` env flag.

    Defaults to **on** — the cross-language validation matrix (38 pinned
    repos, enforced density/orphan/catch-all thresholds, honest degradation
    modes) is the acceptance gate that flipped it. Set ``0``/``false``/``no``/
    ``off`` (case-insensitive) to fall back to the raw uncurated export.
    Resolved at the call site so :func:`curate_knowledge_graph` itself stays
    pure and trivially testable with an explicit ``enabled=``.
    """
    return os.environ.get(_FLAG_ENV, "").strip().lower() not in {"0", "false", "no", "off"}


def curate_knowledge_graph(
    kg: KnowledgeGraphResult,
    *,
    parsed_files: list[Any],
    graph_builder: Any,
    repo_structure: Any,
    community_info: Any,
    git_meta_map: dict[str, dict] | None = None,
    enabled: bool = False,
    defer_summary_floor: bool = False,
) -> KnowledgeGraphResult:
    """Reshape the KG skeleton into an intuitive presentation artifact.

    Pure with respect to the AST graph: reads ``graph_builder`` /
    ``community_info`` but writes only the returned result. When ``enabled`` is
    ``False`` this is a strict no-op returning ``kg`` unchanged (the default, so
    the exported KG is unaffected until the flag flips).

    ``defer_summary_floor`` skips the never-empty summary floor here so it can
    run *after* the wiki-page backfill in generate mode (where richer summaries
    exist); FAST mode leaves it ``False`` so the floor still lands at this seam.

    Each curation step is guarded so that a failure degrades to the prior
    (uncurated) field rather than aborting the export.
    """
    if not enabled:
        return kg

    # Each step mutates only ``kg`` (the presentation result) and is guarded so
    # a failure degrades to the prior, uncurated field rather than aborting the
    # export. Steps are layered in by subsequent phases:
    #   _curate_layers -> _curate_entry_points -> _curate_tour
    #   -> _curate_node_types -> _curate_summaries
    layers_curated = False
    try:
        curated = _curate_layers(kg, graph_builder)
        if curated is not None:
            kg.layers = curated
            layers_curated = True
    except Exception:  # pragma: no cover - defensive; keep uncurated layers
        logger.exception("kg_curation._curate_layers failed; keeping community layers")

    # Wiki modules are a *sibling* artifact of the curated layers (same
    # splitting machinery, module-sized granularity). Only derived when the
    # spine landed — community layers would make the dir-split meaningless,
    # and downstream consumers fall back to community grouping when this
    # stays empty (the fallback matrix's "degraded" row).
    if layers_curated:
        try:
            modules = _curate_modules(kg)
            if modules is not None:
                kg.modules = modules
        except Exception:  # pragma: no cover - defensive; ship no modules
            logger.exception("kg_curation._curate_modules failed; exporting no modules")

    try:
        _curate_entry_points(kg, parsed_files, graph_builder)
    except Exception:  # pragma: no cover - defensive; keep skeleton entry points
        logger.exception("kg_curation._curate_entry_points failed; keeping raw entry points")

    # Genuine churn hotspots (a constantly-edited file off the hot import path)
    # earn a tour stop on this signal alone. Only flagged hotspots with recent
    # commits qualify; repos without git history pass an empty map and the tour
    # is unchanged.
    hotspot_commits = {
        path: int(meta.get("commit_count_90d", 0) or 0)
        for path, meta in (git_meta_map or {}).items()
        if meta.get("is_hotspot")
    }
    try:
        tour = _curate_tour(kg, parsed_files, graph_builder, hotspot_commits=hotspot_commits)
        if tour is not None:
            kg.tour = tour
    except Exception:  # pragma: no cover - defensive; keep skeleton/LLM tour
        logger.exception("kg_curation._curate_tour failed; keeping existing tour")

    try:
        _curate_node_types(kg)
    except Exception:  # pragma: no cover - defensive; keep skeleton types
        logger.exception("kg_curation._curate_node_types failed; keeping coarse types")

    if not defer_summary_floor:
        try:
            apply_summary_floor(kg, parsed_files)
        except Exception:  # pragma: no cover - defensive; leave summaries as-is
            logger.exception("kg_curation summary floor failed; leaving summaries empty")

    return kg


# ---------------------------------------------------------------------------
# Phase 1 — curated layers (replace raw-community layers with the spine)
# ---------------------------------------------------------------------------


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
    file_layers = {
        n["filePath"]: infer_layer(n["filePath"], (n.get("language") or "").lower())
        for n in file_nodes
    }
    import_edges = _file_import_edges(graph_builder)
    order = compute_layer_order(file_layers, import_edges)
    # Honesty label (additive export field): "imports" when inter-layer edges
    # informed the order, "canonical" when it is pure convention — consumers
    # must not claim "X sits above Y" for a canonical order.
    order_basis = layer_order_basis(file_layers, import_edges)

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
            "order_basis": order_basis,
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
# Wiki modules — right-sized directory groups derived from the curated layers
# ---------------------------------------------------------------------------

# Granularity window for derived wiki modules. Sub-groups verbatim are NOT
# module-sized (a 452-file ``core`` sub-group would make one vague mush of a
# doc; a 1-file ``examples`` group would mint a confetti page), so the layer
# node sets are split *recursively* by directory until every group fits the
# window — bottoming out honestly on flat directories. ``target_max`` keeps
# the 10 key-file template slots representative; ``target_min`` is the
# merge-up floor below which a group folds into its nearest sibling.
_MODULE_TARGET_MIN = 8
_MODULE_TARGET_MAX = 120
# A layer smaller than this yields no module at all (matches the selection
# layer's ``min_module_size`` floor that kills singleton pages).
_MODULE_MIN_FILES = 3
# A directory segment present in more than this fraction of all repo paths is
# *generic* (namespace dirs: ``src``, ``packages``, the repo's own name) and
# never appears in a module name. Data-driven — no hardcoded segment list.
_GENERIC_SEGMENT_FRACTION = 0.60
# The legacy community labels' size-suffix dedupe ("ingestion (32)") is the
# exact failure mode module names must never reproduce.
_SIZE_SUFFIX_RE = re.compile(r"\(\d+\)\s*$")


# Universal organizational directory names — containers, not domain labels.
# Shared with community labeling; the data-driven ``dominant_segments`` set
# complements this with per-repo namespace noise (the repo's own name).
GENERIC_ORG_SEGMENTS = frozenset({
    "src", "lib", "core", "common", "shared", "internal", "pkg",
    "main", "app", "utils", "helpers", "index", "mod",
    # Monorepo organisational directories
    "packages", "modules", "workspace", "workspaces", "libs",
    "projects", "services", "apps",
})


def dominant_segments(paths: list[str]) -> set[str]:
    """Directory segments appearing in > 60% of *paths* (namespace noise).

    Shared with community labeling (``analysis/communities.py``) so both
    vocabularies strip the same namespace dirs (``src``, ``packages``, the
    repo's own name) without depending on a hardcoded list.
    """
    n = len(paths)
    if not n:
        return set()
    counts: Counter[str] = Counter()
    for p in paths:
        for seg in set(PurePosixPath(p).parts[:-1]):
            counts[seg] += 1
    return {s for s, c in counts.items() if c / n > _GENERIC_SEGMENT_FRACTION}


def _split_to_granularity(
    node_ids: list[str], id_to_path: dict[str, str], target_max: int
) -> list[tuple[tuple[str, ...], list[str]]]:
    """Recursively split *node_ids* by directory until groups fit *target_max*.

    Returns ``[(dir_segments, sorted_node_ids), ...]``. Reuses ``_sub_split``'s
    prefix logic (group by the first segment that distinguishes members after
    the common directory prefix) but, unlike sub-groups, recurses into any
    group still above ``target_max``. Recursion bottoms out when a directory
    has no distinguishing subdirs — a 200-file flat dir stays one module
    (honest), never an artificial split.
    """
    dir_segs = {nid: PurePosixPath(id_to_path[nid]).parts[:-1] for nid in node_ids}

    def rec(ids: list[str]) -> list[tuple[tuple[str, ...], list[str]]]:
        common = _common_dir_prefix([dir_segs[i] for i in ids])
        if len(ids) <= target_max:
            return [(common, ids)]
        groups: dict[str, list[str]] = defaultdict(list)
        for nid in ids:
            segs = dir_segs[nid]
            key = segs[len(common)] if len(segs) > len(common) else ""
            groups[key].append(nid)
        if len(groups) < 2:
            return [(common, ids)]  # flat directory — no honest split exists
        out: list[tuple[tuple[str, ...], list[str]]] = []
        for key in sorted(groups):
            if key == "":
                # Files sitting directly in the common dir (the "(root)"
                # group). Usually below target_min → folded by merge-up.
                out.append((common, groups[key]))
            else:
                out.extend(rec(groups[key]))
        return out

    return [(d, sorted(ids)) for d, ids in rec(sorted(node_ids))]


def _merge_small_groups(
    groups: list[tuple[tuple[str, ...], list[str]]], target_min: int
) -> list[tuple[tuple[str, ...], list[str]]]:
    """Fold groups below *target_min* into their nearest sibling.

    "Nearest" = the group sharing the longest directory prefix (the parent
    subtree), largest first as the tie-break — so a 2-file "(root)" remnant
    folds into its own subtree's biggest module, and an isolated small dir
    folds into the layer's dominant module rather than minting a confetti
    page. Never merges across layers (callers pass one layer at a time). A
    layer that is itself below ``target_min`` stays one whole group.

    A pre-pass fuses *small sibling* groups into one group at their common
    parent when that collection is itself module-sized — ninety tiny locale
    dirs become one ``conf/locale`` module instead of folding into whichever
    sibling sorts first and misnaming it. The fold-in loop then never renames
    a survivor: a healthy ``core/providers`` absorbing a 2-file sibling keeps
    its identity.
    """
    merged = [(d, list(ids)) for d, ids in groups]

    by_parent: dict[tuple[str, ...], list[tuple[tuple[str, ...], list[str]]]] = {}
    for g in merged:
        if len(g[1]) < target_min and len(g[0]) > 0:
            by_parent.setdefault(g[0][:-1], []).append(g)
    for parent, sibs in sorted(by_parent.items()):
        if len(sibs) < 2 or sum(len(g[1]) for g in sibs) < target_min:
            continue
        fused = sorted(nid for g in sibs for nid in g[1])
        for g in sibs:
            merged.remove(g)
        existing = next((g for g in merged if g[0] == parent), None)
        if existing is not None:
            existing[1].extend(fused)
            existing[1].sort()
        else:
            merged.append((parent, fused))
    merged.sort(key=lambda g: g[0])

    def shared(a: tuple[str, ...], b: tuple[str, ...]) -> int:
        return len(_common_dir_prefix([a, b]))

    while len(merged) > 1:
        small = min(
            (g for g in merged if len(g[1]) < target_min),
            key=lambda g: (len(g[1]), g[0]),
            default=None,
        )
        if small is None:
            break
        merged.remove(small)
        target = min(
            merged,
            key=lambda g: (-shared(g[0], small[0]), -len(g[1]), g[0]),
        )
        target[1].extend(small[1])
        target[1].sort()
    return [(d, ids) for d, ids in merged]


def _name_modules(mods: list[dict], generic: set[str]) -> None:
    """Assign unique, human module names in place.

    Initial name = the last one or two *informative* directory segments
    (generic namespace segments stripped; when stripping consumes every
    segment, the raw tail is used instead). Collisions extend leftward by
    one more parent segment — NEVER a size suffix. Single-module layers
    take the layer's name; the root group (empty dir) becomes
    "<Layer> (top-level)". The absolute fallback (identical informative
    paths across layers) appends the layer name, which is unique by
    construction.
    """
    per_layer: Counter[str] = Counter(m["layerId"] for m in mods)
    info_by: dict[int, list[str]] = {}
    used: dict[int, int | None] = {}  # informative segments consumed; None = fixed
    for m in mods:
        # Data-driven stripping can consume EVERY segment on fixture-dominated
        # repos (aeson: tests/JSONTestSuite/test_parsing is >60% of all
        # paths). The raw dir tail is still the honest name there —
        # "(top-level)" would mislabel a real directory and collide across
        # sibling groups (which trips the export degradation guard and ships
        # no modules). Universal organizational dirs (pkg, src, packages…)
        # stay excluded even in the fallback: "(top-level)" reads better than
        # a container name, so it remains the name for true root groups.
        info = [s for s in m["_dir"] if s not in generic] or [
            s for s in m["_dir"] if s.lower() not in GENERIC_ORG_SEGMENTS
        ]
        info_by[id(m)] = info
        if per_layer[m["layerId"]] == 1:
            m["name"] = m["_layerName"]
            used[id(m)] = None
        elif not info:
            m["name"] = f"{m['_layerName']} (top-level)"
            used[id(m)] = None
        else:
            k = min(2, len(info))
            m["name"] = "/".join(info[-k:])
            used[id(m)] = k

    for _ in range(16):  # bounded: each round consumes ≥1 segment somewhere
        names = Counter(m["name"] for m in mods)
        colliding = [m for m in mods if names[m["name"]] > 1]
        if not colliding:
            return
        progressed = False
        for m in colliding:
            k = used.get(id(m))
            info = info_by[id(m)]
            if k is not None and k < len(info):
                used[id(m)] = k + 1
                m["name"] = "/".join(info[-(k + 1) :])
                progressed = True
        if not progressed:
            break

    # Two all-organizational groups in one layer (a root remnant plus a
    # "packages"-style container) would both read "<Layer> (top-level)" —
    # the container's raw tail is the honest tiebreak.
    names = Counter(m["name"] for m in mods)
    for m in mods:
        if names[m["name"]] > 1 and not info_by[id(m)] and m["_dir"]:
            m["name"] = "/".join(m["_dir"][-min(2, len(m["_dir"])) :])

    # Same informative dir in two layers (or no segments left): the layer
    # name disambiguates — (dir, layer) is unique by construction.
    names = Counter(m["name"] for m in mods)
    for m in mods:
        if names[m["name"]] > 1:
            m["name"] = f"{m['name']} ({m['_layerName']})"

    # Absolute backstop (two all-org dirs in one layer sharing a tail): the
    # full dir path is unique per layer.
    names = Counter(m["name"] for m in mods)
    for m in mods:
        if names[m["name"]] > 1 and m["_dir"]:
            m["name"] = "/".join(m["_dir"])


def derive_modules(
    layers: list[dict],
    id_to_path: dict[str, str],
    *,
    target_min: int = _MODULE_TARGET_MIN,
    target_max: int = _MODULE_TARGET_MAX,
    min_module_size: int = _MODULE_MIN_FILES,
    lang_by_id: dict[str, str] | None = None,
) -> list[dict]:
    """Derive right-sized, stably-identified wiki modules from curated layers.

    ``Module = {"id": "module:<dir-slug>", "name": <human>, "path": <dir or "">,
    "layerId": ..., "nodeIds": [...], "language": ...}``

    Properties (each one an edge case from the research pass):

    - **Partition per layer**: every node of every layer ≥ ``min_module_size``
      lands in exactly one module; layers below the floor yield none. Never
      merges across layers.
    - **Granularity**: recursive directory splitting to the
      [``target_min``, ``target_max``] window; flat dirs stay one honest
      module; sub-``target_min`` remnants merge up into their subtree.
    - **Names**: informative path segments only (data-driven generic-segment
      stripping kills ``src``/``packages``/repo-name automatically); collision
      resolution extends the path leftward — never a size suffix.
    - **Ids**: ``module:`` + slug of the real directory path — stable across
      runs and under file adds/renames inside the dir; changes only when the
      directory itself moves. ``path`` is the actual dir (not the slug) so
      path-prefix child lookups (``target_path LIKE 'dir/%'``) work.
    - **Files only**: operates on ids present in ``id_to_path`` — external
      nodes never pollute a module.
    - **Determinism**: sorted iteration throughout; same inputs → same bytes.
    """
    generic = dominant_segments(sorted(set(id_to_path.values())))

    mods: list[dict] = []
    for layer in layers:
        node_ids = [nid for nid in layer.get("nodeIds", []) if nid in id_to_path]
        if len(node_ids) < min_module_size:
            continue
        groups = _merge_small_groups(
            _split_to_granularity(node_ids, id_to_path, target_max), target_min
        )
        for dir_parts, ids in sorted(groups):
            mods.append(
                {
                    "_dir": dir_parts,
                    "_layerName": layer.get("name", ""),
                    "path": "/".join(dir_parts),
                    "layerId": layer.get("id", ""),
                    "nodeIds": sorted(ids),
                }
            )

    _name_modules(mods, generic)

    # Ids: path-derived slugs; the bigger module keeps the plain id on the
    # rare cross-layer dir collision (a dir whose files split across layers).
    used_ids: set[str] = set()
    for m in sorted(mods, key=lambda m: (-len(m["nodeIds"]), m["path"], m["layerId"])):
        base = "module:" + _slugify(m["path"] or m["_layerName"])
        mid = base
        n = 1
        while mid in used_ids:
            mid = f"{base}--{_slugify(m['_layerName'])}" + ("" if n == 1 else f"-{n}")
            n += 1
        used_ids.add(mid)
        m["id"] = mid

    # A single-module layer is 1:1 with its layer page — mark it so page
    # generation can skip the duplicate doc (the module stays in the
    # artifact: canvas containers and the coverage invariant need it).
    per_layer_count: Counter[str] = Counter(m["layerId"] for m in mods)

    out: list[dict] = []
    for m in mods:
        module = {
            "id": m["id"],
            "name": m["name"],
            "path": m["path"],
            "layerId": m["layerId"],
            "nodeIds": m["nodeIds"],
        }
        if per_layer_count[m["layerId"]] == 1:
            module["wholeLayer"] = True
        if lang_by_id is not None:
            langs = Counter(
                lang for nid in m["nodeIds"] if (lang := lang_by_id.get(nid, ""))
            )
            module["language"] = (
                min(langs, key=lambda tag: (-langs[tag], tag)) if langs else ""
            )
        out.append(module)
    return out


def _curate_modules(kg: KnowledgeGraphResult) -> list[dict] | None:
    """Derive wiki modules from the curated layers, or ``None`` on degradation.

    Mirrors ``_curate_layers``' honest-degradation guard: a partition or
    uniqueness violation ships *no* modules (consumers fall back to community
    grouping) rather than a broken artifact.
    """
    file_nodes = _file_nodes(kg)
    if not file_nodes:
        return None
    id_to_path = {n["id"]: n["filePath"] for n in file_nodes}
    lang_by_id = {n["id"]: (n.get("language") or "").lower() for n in file_nodes}

    modules = derive_modules(kg.layers, id_to_path, lang_by_id=lang_by_id)
    if not modules:
        return None

    seen: set[str] = set()
    for m in modules:
        for nid in m["nodeIds"]:
            if nid in seen or nid not in id_to_path:
                logger.warning(
                    "kg_curation: derived modules failed partition invariant; "
                    "exporting no modules"
                )
                return None
            seen.add(nid)
    names = [m["name"] for m in modules]
    ids = [m["id"] for m in modules]
    if len(set(names)) != len(names) or len(set(ids)) != len(ids):
        logger.warning(
            "kg_curation: derived module names/ids not unique; exporting no modules"
        )
        return None
    return modules


# ---------------------------------------------------------------------------
# Phase 2 — entry-point precision (demote barrels, rank + cap survivors)
# ---------------------------------------------------------------------------


def _curate_entry_points(
    kg: KnowledgeGraphResult, parsed_files: list[Any], graph_builder: Any
) -> None:
    """Demote re-export barrels and surface a capped, ranked entry-point set.

    Mutates only the presentation view: drops the ``entry_point`` *tag* from
    barrel nodes (and adds a ``barrel`` tag) without touching the AST graph's
    ``is_entry_point`` flag (the dead-code pass relies on it). Survivors are
    ranked by :func:`rank_entry_points` — execution-start evidence (a
    conventional entry name, shallow path) first, centrality only as a tiebreak
    — so a deeply-nested resolver ``index.py`` cannot outrank the real
    ``main.py``. Config/data files (``server.json``) and generic-glue leaves
    (a resolver's deep ``index.py``) are dropped from candidacy outright.
    ``project.entry_points`` holds the top few, ``project.entry_candidates`` the
    full ranked list. When ingestion flagged no entries at all, the strong
    :func:`score_entry_points` scorers (entry-style filenames) fill in, so the
    orientation panel never opens empty on repos without a detectable main.
    """
    pf_by_path = {pf.file_info.path: pf for pf in parsed_files if getattr(pf, "file_info", None)}
    lang_by_path = {n["filePath"]: (n.get("language") or "").lower() for n in _file_nodes(kg)}
    pagerank = graph_builder.pagerank() or {}
    try:
        betweenness = graph_builder.betweenness_centrality() or {}
    except Exception:  # pragma: no cover - defensive
        betweenness = {}

    candidates: list[tuple[str, float, float]] = []
    for node in kg.nodes:
        nid = node.get("id", "")
        if not (isinstance(nid, str) and nid.startswith("file:")):
            continue
        tags = node.get("tags") or []
        if "entry_point" not in tags:
            continue
        path = node.get("filePath", "")
        language = (node.get("language") or "").lower()
        if infer_layer(path, language) in ADJACENT_LAYERS or is_support_path(path):
            # Test fixtures (a wsgi.py inside tests/) and sample programs
            # (examples/*/main.go) may carry the ingestion flag, but they are
            # not where a reader enters the system.
            continue
        pf = pf_by_path.get(path)
        if pf is not None and _is_barrel(pf):
            new_tags = [t for t in tags if t != "entry_point"]
            if "barrel" not in new_tags:
                new_tags.append("barrel")
            node["tags"] = new_tags
            continue
        if not_an_execution_start(path, language):
            continue
        candidates.append((path, pagerank.get(path, 0.0), betweenness.get(path, 0.0)))

    if not candidates:
        # No ingestion-flagged entries (or all were barrels): fall back to the
        # strong filename scorers the tour seeds from (score >= 3 means an
        # entry-style name or flag, never just shallow/high-PageRank).
        for s, path in score_entry_points(parsed_files, pagerank):
            if s < 3.0:
                continue
            language = lang_by_path.get(path, "")
            if infer_layer(path, language) in ADJACENT_LAYERS or is_support_path(path):
                continue
            pf = pf_by_path.get(path)
            if pf is not None and _is_barrel(pf):
                continue
            if not_an_execution_start(path, language):
                continue
            candidates.append((path, pagerank.get(path, 0.0), betweenness.get(path, 0.0)))

    ranked = rank_entry_points(candidates, conventional_entry_stems())
    kg.project["entry_points"] = ranked[:_MAX_ENTRY_POINTS]
    kg.project["entry_candidates"] = ranked


# ---------------------------------------------------------------------------
# Phase 4 — node typing & never-empty summaries
# ---------------------------------------------------------------------------

# Path signals for richer node typing than the skeleton's coarse
# file/config/service/document. These run only in the presentation view; the
# AST graph node_type used elsewhere is untouched.
_CI_PATH_MARKERS = (
    ".github/workflows/",
    ".gitlab-ci",
    ".circleci/",
    "azure-pipelines",
    "jenkinsfile",
    "bitbucket-pipelines",
)
_INFRA_NAME_MARKERS = ("dockerfile", "docker-compose", "compose.yaml", "compose.yml")
_INFRA_PATH_MARKERS = ("/k8s/", "/kubernetes/", "/helm/", "/terraform/")
_INFRA_SUFFIXES = (".tf", ".hcl")
_DATA_PATH_MARKERS = ("/migrations/", "/migration/")
_DATA_SUFFIXES = (".sql", ".prisma")

# Source-code extensions. A code file is never CI/infra config however its
# name or directory reads — ``languages/specs/dockerfile.py`` *parses*
# Dockerfiles, it isn't one. Registry-derived: every is_code,
# non-infra language's extensions are protected — .dart/.hs/.clj included;
# shell/terraform stay promotable (they ARE infra); the historical orphan
# ``.pl`` (no perl spec) is gone.
_CODE_SUFFIXES = _LANG_REGISTRY.non_infra_code_extensions()


def _enrich_type(path: str, current_type: str) -> tuple[str, str | None]:
    """Return a richer ``(type, extra_tag)`` for a file node, or keep current.

    The tag (``ci``/``infra``/``data``) is additive; ``None`` means no new tag.
    Name/path markers never fire for source-code files (``_CODE_SUFFIXES``);
    only genuine config artifacts get promoted.
    """
    p = path.lower()
    name = PurePosixPath(p).name
    suffix = PurePosixPath(p).suffix
    is_code = suffix in _CODE_SUFFIXES

    if not is_code and (any(m in p for m in _CI_PATH_MARKERS) or name == "jenkinsfile"):
        return "pipeline", "ci"
    if (
        not is_code
        and (
            name.startswith("dockerfile")
            or any(m in name for m in _INFRA_NAME_MARKERS)
            or any(m in p for m in _INFRA_PATH_MARKERS)
        )
    ) or suffix in _INFRA_SUFFIXES:
        return "service", "infra"
    if any(m in p for m in _DATA_PATH_MARKERS) or suffix in _DATA_SUFFIXES:
        return "schema", "data"
    return current_type, None


def _curate_node_types(kg: KnowledgeGraphResult) -> None:
    """Promote infra/CI/data file nodes to first-class presentation types."""
    for node in _file_nodes(kg):
        new_type, tag = _enrich_type(node["filePath"], node.get("type", "file"))
        if new_type != node.get("type"):
            node["type"] = new_type
        if tag:
            tags = node.setdefault("tags", [])
            if tag not in tags:
                tags.append(tag)


def _infer_test_target(path: str) -> str:
    """Best-effort name of what a test file covers (strip test markers)."""
    stem = PurePosixPath(path).stem
    for marker in (".test", ".spec", "_test", "test_", "_spec", "spec_"):
        if marker in stem.lower():
            cleaned = stem.lower().replace(marker, "")
            return cleaned.strip("_.- ") or stem
    return stem


def _cheap_summary(node: dict, parsed_file: Any | None) -> str:
    """A deterministic, honest fallback summary (zero LLM cost)."""
    path = node["filePath"]
    stem = PurePosixPath(path).stem
    parent = PurePosixPath(path).parent.name or "root"
    node_type = node.get("type", "file")
    tags = node.get("tags") or []
    layer = infer_layer(path, (node.get("language") or "").lower())

    if "barrel" in tags:
        return f"Re-export barrel for {parent}/."

    name = PurePosixPath(path).name
    if node_type in {"pipeline", "service", "schema", "config", "document"} or (
        tags and ({"ci", "infra", "data", "config"} & set(tags))
    ):
        # Recognised scaffolding earns a real role instead of a bare name
        # restatement; only genuinely opaque support files fall back to the
        # type template below.
        role = well_known_role(path)
        if role is not None:
            return role
    if node_type == "pipeline" or "ci" in tags:
        return f"CI / pipeline definition: {name}."
    if node_type == "service" or "infra" in tags:
        return f"Infrastructure definition: {name}."
    if node_type == "schema" or "data" in tags:
        return f"Data / schema definition: {name}."
    if node_type == "config" or "config" in tags:
        return f"Configuration file: {name}."
    if node_type == "document":
        return f"Documentation: {name}."
    if "test" in tags:
        return f"Tests for {_infer_test_target(path)}."

    # Code file: name the layer and its most prominent symbols.
    symbol_names: list[str] = []
    if parsed_file is not None:
        symbol_names = [
            getattr(s, "name", "")
            for s in (getattr(parsed_file, "symbols", []) or [])
            if getattr(s, "kind", "") in _SUBSTANTIVE_KINDS and getattr(s, "name", "")
        ][:3]
    if symbol_names:
        return f"{layer} module {stem} defining {', '.join(symbol_names)}."
    count = node.get("symbolCount", 0)
    if count:
        return f"{layer} module {stem} ({count} symbols)."
    return f"{layer} module {stem}."


def apply_summary_floor(kg: KnowledgeGraphResult, parsed_files: list[Any] | None = None) -> None:
    """Ensure every file node carries a summary (cheap deterministic floor).

    Idempotent and never clobbering: only fills nodes whose summary is still
    empty, so a richer wiki-page summary (backfilled before this runs in
    generate mode) always wins. ``parsed_files`` is optional — when absent the
    fallback uses the node's symbol count instead of naming top symbols.
    """
    pf_by_path = {
        pf.file_info.path: pf for pf in (parsed_files or []) if getattr(pf, "file_info", None)
    }
    for node in _file_nodes(kg):
        if node.get("summary"):
            continue
        node["summary"] = _cheap_summary(node, pf_by_path.get(node["filePath"]))


# ---------------------------------------------------------------------------
# Phase 7 — invariant validation (shared by tests and the portable writer)
# ---------------------------------------------------------------------------

# Quality thresholds. The lower layer bound and coverage targets are *soft*
# (warnings) because they depend on repo size/shape; the partition, hard count
# bound, capped entry set, never-empty summaries, and tour budget are *hard*.
_MIN_LAYERS = 6
_MAX_LAYER_FRACTION = 0.35
_MAX_CATCHALL_FRACTION = 0.20
_MAX_SINGLETON_FRACTION = 0.10
_MIN_TOUR_COVERAGE = 0.90


@dataclass
class KGValidation:
    """Outcome of :func:`validate_kg` — hard errors, soft warnings, metrics."""

    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "metrics": self.metrics,
        }


def validate_kg(kg: KnowledgeGraphResult) -> KGValidation:
    """Validate a curated KG against the intuitiveness invariants (plan §5/§7).

    Pure and side-effect free. Hard violations set ``ok=False`` and populate
    ``errors``; size/shape-dependent shortfalls go to ``warnings``. The
    ``metrics`` block is the per-repo intuitiveness scorecard.
    """
    errors: list[str] = []
    warnings: list[str] = []

    file_nodes = _file_nodes(kg)
    file_count = len(file_nodes)
    file_ids = {n["id"] for n in file_nodes}
    tags_by_path = {n["filePath"]: (n.get("tags") or []) for n in file_nodes}
    summary_by_id = {n["id"]: n.get("summary") for n in file_nodes}

    layers = kg.layers or []
    n_layers = len(layers)

    # -- Layer count -------------------------------------------------------
    if n_layers == 0:
        errors.append("no layers")
    elif n_layers > _MAX_LAYERS:
        errors.append(f"too many layers: {n_layers} > {_MAX_LAYERS}")
    elif n_layers < _MIN_LAYERS:
        warnings.append(f"few layers: {n_layers} < {_MIN_LAYERS} (small/flat repo?)")

    # -- Partition ---------------------------------------------------------
    layered: list[str] = [nid for layer in layers for nid in layer.get("nodeIds", [])]
    layered_set = set(layered)
    if len(layered) != len(layered_set):
        errors.append("partition: a file appears in more than one layer")
    if file_count and layered_set != file_ids:
        missing = len(file_ids - layered_set)
        extra = len(layered_set - file_ids)
        errors.append(f"partition: {missing} unlayered, {extra} unknown ids")

    # -- Singleton spam & mega-layer balance -------------------------------
    sizes = [len(layer.get("nodeIds", [])) for layer in layers]
    singleton_frac = (sum(1 for s in sizes if s == 1) / n_layers) if n_layers else 0.0
    if singleton_frac >= _MAX_SINGLETON_FRACTION:
        warnings.append(f"singleton layers {singleton_frac:.0%} ≥ {_MAX_SINGLETON_FRACTION:.0%}")

    largest_frac = (max(sizes) / file_count) if (sizes and file_count) else 0.0
    if largest_frac > _MAX_LAYER_FRACTION:
        warnings.append(f"largest layer {largest_frac:.0%} > {_MAX_LAYER_FRACTION:.0%}")

    catchall = next((layer for layer in layers if layer.get("name") == "Application"), None)
    catchall_frac = (
        (len(catchall.get("nodeIds", [])) / file_count) if (catchall and file_count) else 0.0
    )
    if catchall_frac > _MAX_CATCHALL_FRACTION:
        warnings.append(f"Application catch-all {catchall_frac:.0%} > {_MAX_CATCHALL_FRACTION:.0%}")

    # -- Entry points ------------------------------------------------------
    entry_points = kg.project.get("entry_points", []) if isinstance(kg.project, dict) else []
    if len(entry_points) > _MAX_ENTRY_POINTS:
        errors.append(f"too many entry points: {len(entry_points)} > {_MAX_ENTRY_POINTS}")
    barrels_surfaced = [p for p in entry_points if "barrel" in tags_by_path.get(p, [])]
    if barrels_surfaced:
        errors.append(f"barrels surfaced as entry points: {barrels_surfaced}")

    # -- Tour --------------------------------------------------------------
    tour = kg.tour or []
    tour_coverage = 0.0
    if tour:
        if len(tour) > DEFAULT_MAX_STOPS:
            errors.append(f"tour too long: {len(tour)} > {DEFAULT_MAX_STOPS}")
        if tour[0].get("kind") != "overview":
            errors.append("tour does not open with an overview/README step")
        layer_ids = {layer.get("id") for layer in layers}
        covered = {
            s.get("layer_id")
            for s in tour
            if s.get("kind") != "overview" and s.get("layer_id") in layer_ids
        }
        tour_coverage = (len(covered) / len(layer_ids)) if layer_ids else 0.0
        if tour_coverage < _MIN_TOUR_COVERAGE:
            warnings.append(f"tour covers {tour_coverage:.0%} of layers < {_MIN_TOUR_COVERAGE:.0%}")

    # -- Modules (only when the curated artifact carries them) -------------
    modules = getattr(kg, "modules", None) or []
    module_covered: set[str] = set()
    if modules:
        module_member_lists = [m.get("nodeIds", []) for m in modules]
        flat = [nid for ids in module_member_lists for nid in ids]
        module_covered = set(flat)
        if len(flat) != len(module_covered):
            errors.append("modules: a file appears in more than one module")
        if not module_covered <= file_ids:
            errors.append(
                f"modules: {len(module_covered - file_ids)} unknown ids in modules"
            )
        module_names = [m.get("name", "") for m in modules]
        if len(set(module_names)) != len(module_names):
            errors.append("modules: names not unique")
        size_suffixed = [n for n in module_names if _SIZE_SUFFIX_RE.search(n)]
        if size_suffixed:
            errors.append(f"modules: size-suffixed names: {size_suffixed}")
        oversized = sum(
            1 for ids in module_member_lists if len(ids) > _MODULE_TARGET_MAX
        )
        if oversized:
            # Flat dirs may honestly exceed the window — soft signal only.
            warnings.append(f"{oversized} modules above target_max (flat dirs?)")

    # -- Summaries ---------------------------------------------------------
    empty_summaries = [nid for nid, s in summary_by_id.items() if not s]
    if empty_summaries:
        errors.append(f"{len(empty_summaries)} file nodes have an empty summary")
    summary_completeness = 1.0 - len(empty_summaries) / file_count if file_count else 1.0

    metrics = {
        "file_count": file_count,
        "layer_count": n_layers,
        "module_count": len(modules),
        "module_coverage_pct": round(
            (len(module_covered) / file_count * 100) if (modules and file_count) else 0.0, 1
        ),
        "singleton_layer_pct": round(singleton_frac * 100, 1),
        "largest_layer_pct": round(largest_frac * 100, 1),
        "application_pct": round(catchall_frac * 100, 1),
        "entry_point_count": len(entry_points),
        "tour_steps": len(tour),
        "tour_coverage_pct": round(tour_coverage * 100, 1),
        "summary_completeness_pct": round(summary_completeness * 100, 1),
    }

    return KGValidation(ok=not errors, errors=errors, warnings=warnings, metrics=metrics)


# ---------------------------------------------------------------------------
# Phase 6 — portable, self-validated export artifact
# ---------------------------------------------------------------------------


def build_portable_kg(kg: KnowledgeGraphResult) -> tuple[dict, KGValidation]:
    """Assemble a self-contained, self-validated ``knowledge-graph.json`` dict.

    Kept separate from :meth:`KnowledgeGraphResult.to_dict` so the *default*
    export stays byte-identical (curation flag-off contract); the portable
    artifact adds a ``meta`` block (counts, fingerprint) and an embedded
    ``validation`` report so an external consumer can trust it without a server.
    Returns ``(data, validation)`` so the writer can decide on hard violations.
    """
    data = kg.to_dict()
    validation = validate_kg(kg)
    data["meta"] = {
        # The integer the loader gates on, not the "1.0.0" display label.
        "schema_version": data.get("schema_version", 1),
        "generator": "repowise-kg-curation",
        "fingerprint": getattr(kg, "fingerprint", ""),
        "file_count": validation.metrics.get("file_count", 0),
        "layer_count": validation.metrics.get("layer_count", 0),
        "module_count": validation.metrics.get("module_count", 0),
        "entry_point_count": validation.metrics.get("entry_point_count", 0),
        "tour_steps": validation.metrics.get("tour_steps", 0),
        "validation": validation.as_dict(),
    }
    return data, validation
