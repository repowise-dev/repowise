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

from repowise.core.analysis.knowledge_graph import KnowledgeGraphResult, _slugify
from repowise.core.generation.layers import (
    ADJACENT_LAYERS,
    compute_layer_order,
    infer_layer,
    is_support_path,
    layer_order_basis,
)
from repowise.core.generation.tour import (
    DEFAULT_MAX_STOPS,
    build_tour,
    score_entry_points,
)
from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY

# Closing-stop anchors (conftest, spec_helper, test_helper, …) and
# declaration descriptors (module-info.java) — both registry-declared.
_SUITE_ANCHOR_STEMS: frozenset[str] = _LANG_REGISTRY.suite_anchor_stems()
_DESCRIPTOR_FILENAMES: frozenset[str] = _LANG_REGISTRY.descriptor_filenames()
# Test-fixture filename shapes (FooFixtures.java) — case-sensitive,
# per-extension; fixture files hold test data, they never face the suite.
_FIXTURE_CAMEL_RES = _LANG_REGISTRY.camel_fixture_res_by_extension()
# Test-project dir suffixes (.Tests/.Specs) — when present, the suite's
# face must come from inside one.
_TEST_PROJECT_DIR_SUFFIXES: tuple[str, ...] = _LANG_REGISTRY.test_dir_suffixes()

# Honest-degradation thresholds. Density = (imports + tested_by)
# edges per dominant-language file — the same definition the validation
# harness uses, calibrated on the 13-repo matrix: express (1.89, broken CJS
# resolution) and sinatra (1.48, broken require resolution) land in
# "sparse"; every healthy repo sits at ≥ 2.2. Repos below the file floor
# skip the density check — density on a 7-file repo is noise, not evidence.
# Low density alone stops indicting the resolver once the resolution rate
# (internal targets / all targets) is strong: stdlib filtering makes an
# honestly-resolved Ruby gem land at ~1.3 edges/file with 0.77 resolution —
# that graph isn't lying, it's just require-light.
_FLOW_DENSITY_FLOOR = 2.0
_FLOW_RESOLUTION_FLOOR = 0.7
_STRUCTURAL_DENSITY_FLOOR = 0.3
_MODE_MIN_FILES = 25


def _is_fixture_shaped(path: str) -> bool:
    """True when the filename matches its language's fixture convention."""
    pp = PurePosixPath(path)
    fixture_re = _FIXTURE_CAMEL_RES.get(pp.suffix.lower())
    return fixture_re is not None and fixture_re.search(pp.stem) is not None


def _graph_mode(dominant_lang: str, lang_by_path: dict[str, str], graph_builder: Any) -> str:
    """Classify how much the import graph can honestly claim.

    ``flow``       — full resolver support and healthy density: the tour may
                     narrate execution flow.
    ``sparse``     — partial support, or full support with suspiciously low
                     density: BFS still walks, but reasons must not blame
                     files for the resolver's gaps.
    ``structural`` — no resolver (or a near-edgeless graph): no execution
                     claims at all; the tour walks the repo's structure.
    """
    support = _LANG_REGISTRY.import_support_for(dominant_lang)
    if support == "none":
        return "structural"
    dom_files = {p for p, lang in lang_by_path.items() if lang == dominant_lang}
    if not dom_files:
        return "structural"
    edge_count = 0
    internal_targets = 0
    external_targets = 0
    try:
        for src, dst, data in graph_builder.graph().edges(data=True):
            if (data or {}).get("edge_type") in ("imports", "tested_by") and src in dom_files:
                edge_count += 1
                if isinstance(dst, str) and dst.startswith("external:"):
                    external_targets += 1
                else:
                    internal_targets += 1
    except Exception:  # pragma: no cover - defensive
        return "flow" if support == "full" else "sparse"
    total = internal_targets + external_targets
    resolution = (internal_targets / total) if total else 0.0
    if len(dom_files) < _MODE_MIN_FILES:
        # Density is unmeasurable on tiny repos, but resolution is not: a
        # partial-tier repo whose imports resolve cleanly must not have its
        # tour blame "incomplete import resolution" just for being small.
        if support == "full" or (total and resolution >= _FLOW_RESOLUTION_FLOOR):
            return "flow"
        return "sparse"
    density = edge_count / len(dom_files)
    if density < _STRUCTURAL_DENSITY_FLOOR:
        return "structural"
    # Partial-tier languages run in flow or sparse per their REAL density
    # and resolution, exactly like full-tier ones: a regex-tier resolver
    # that resolves 0.95+ of an Elixir repo's aliases must not have its
    # tour blame "incomplete import resolution" — that would be the lie
    # this mode exists to prevent, inverted.
    # Low density indicts the resolver only when resolution is ALSO
    # weak — a require-light but well-resolved graph narrates honestly.
    if density < _FLOW_DENSITY_FLOOR and resolution < _FLOW_RESOLUTION_FLOOR:
        return "sparse"
    return "flow"

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

    try:
        tour = _curate_tour(kg, parsed_files, graph_builder)
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

    ``imports`` edges only (hint-sourced ones included — they are import
    semantics). The raw graph also carries ``contains`` (file → symbol),
    ``co_change``, ``calls``, and heritage edges; letting those through made
    "execution flow" claims ride on symbol counts and change-history
    coupling — a giant declarations header would out-rank every real entry
    as the walk's widest-fan-out anchor. Externals are naturally ignored
    downstream by :func:`compute_layer_order`, which only counts edges whose
    endpoints are both in ``file_layers``.
    """
    edges: list[tuple[str, str]] = []
    try:
        g = graph_builder.graph()
        for src, dst, data in g.edges(data=True):
            if not (isinstance(src, str) and isinstance(dst, str)):
                continue
            if data.get("edge_type", "imports") != "imports":
                continue
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


def _dominant_language(code_langs: list[str]) -> str:
    """Most common language, ties broken deterministically.

    ``Counter.most_common`` breaks count ties by insertion order, which is
    thread-completion-order nondeterministic here — and the result reaches
    persisted output (``project.graph_mode`` plus tour prose). Tie-break by
    count descending, then language name ascending, so the same KG always
    yields the same dominant language regardless of ingestion ordering.
    """
    if not code_langs:
        return ""
    return min(Counter(code_langs).items(), key=lambda kv: (-kv[1], kv[0]))[0]


def _curate_entry_points(
    kg: KnowledgeGraphResult, parsed_files: list[Any], graph_builder: Any
) -> None:
    """Demote re-export barrels and surface a capped, ranked entry-point set.

    Mutates only the presentation view: drops the ``entry_point`` *tag* from
    barrel nodes (and adds a ``barrel`` tag) without touching the AST graph's
    ``is_entry_point`` flag (the dead-code pass relies on it). Survivors are
    ranked by ``pagerank + betweenness``; ``project.entry_points`` holds the top
    few, ``project.entry_candidates`` the full ranked list. When ingestion
    flagged no entries at all, the strong :func:`score_entry_points` scorers
    (entry-style filenames) fill in, so the orientation panel never opens empty
    on repos without a detectable main.
    """
    pf_by_path = {pf.file_info.path: pf for pf in parsed_files if getattr(pf, "file_info", None)}
    lang_by_path = {n["filePath"]: (n.get("language") or "").lower() for n in _file_nodes(kg)}
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
        if infer_layer(path, node.get("language")) in ADJACENT_LAYERS or is_support_path(path):
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
        score = pagerank.get(path, 0.0) + betweenness.get(path, 0.0)
        survivors.append((score, path))

    if not survivors:
        # No ingestion-flagged entries (or all were barrels): fall back to the
        # strong filename scorers the tour seeds from (score >= 3 means an
        # entry-style name or flag, never just shallow/high-PageRank).
        for s, path in score_entry_points(parsed_files, pagerank):
            if s < 3.0:
                continue
            if infer_layer(path, lang_by_path.get(path)) in ADJACENT_LAYERS or is_support_path(path):
                continue
            pf = pf_by_path.get(path)
            if pf is not None and _is_barrel(pf):
                continue
            score = pagerank.get(path, 0.0) + betweenness.get(path, 0.0)
            survivors.append((score, path))

    # Highest score first; path as a stable, deterministic tie-break.
    survivors.sort(key=lambda sp: (-sp[0], sp[1]))
    ranked = [path for _, path in survivors]
    kg.project["entry_points"] = ranked[:_MAX_ENTRY_POINTS]
    kg.project["entry_candidates"] = ranked


# ---------------------------------------------------------------------------
# Phase 3 — canonical execution-flow tour
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


def _structural_walk(
    universe: list[str],
    type_by_path: dict[str, str],
    dominant_lang: str,
    pagerank: dict[str, float],
    graph_builder: Any,
    project_name: str = "",
) -> tuple[list[str], dict[str, str]]:
    """Anchor + directory faces for repos with no usable import graph.

    No execution-flow claims: the anchor is ranked by whatever evidence
    exists (PageRank over the full graph — co-change/dynamic edges included
    — then fan-in, shallowness, path), never alphabetically-first-by-luck;
    the walk visits the largest top-level code areas, one face each. Every
    reason says what the evidence is and what is missing.
    """
    # Manifests (mix.exs, setup.py) are code-shaped but describe the
    # project rather than implement it — never the place to start reading.
    manifests = _LANG_REGISTRY.manifest_filenames()
    code = [
        p
        for p in universe
        if type_by_path.get(p) not in {"config", "document"}
        and PurePosixPath(p).name not in manifests
    ]
    if not code:
        return [], {}

    fan_in: Counter[str] = Counter()
    for _src, dst in _file_import_edges(graph_builder):
        fan_in[dst] += 1

    spec = _LANG_REGISTRY.get(dominant_lang)
    display = spec.display_name if spec else (dominant_lang or "this language")

    # Conventional names trump raw connectivity: an entry-named file
    # (application.ex, Main.hs) or the project-named module (lib/jason.ex in
    # jason — the library-main convention) is where a reader starts.
    entry_names = _LANG_REGISTRY.entry_point_names()
    project_stem = (project_name or "").lower()

    def conventional(p: str) -> bool:
        pp = PurePosixPath(p)
        return pp.name in entry_names or (
            bool(project_stem) and pp.stem.lower() == project_stem
        )

    anchor = min(
        code,
        key=lambda p: (
            not conventional(p),
            -pagerank.get(p, 0.0),
            -fan_in.get(p, 0),
            len(PurePosixPath(p).parts),
            p,
        ),
    )
    if PurePosixPath(anchor).name in entry_names:
        anchor_reason = (
            f"Named like an entry file — the conventional place {display} "
            "execution starts. Import analysis isn't supported for "
            f"{display} yet, so the walk follows the repo's structure."
        )
    elif conventional(anchor):
        anchor_reason = (
            "Named after the project — by convention the library's main "
            f"module. Import analysis isn't supported for {display} yet, "
            "so the walk follows the repo's structure."
        )
    else:
        anchor_reason = (
            "The best-connected file by the evidence available (change "
            f"history and references). Import analysis isn't supported for "
            f"{display} yet, so the walk follows the repo's structure."
        )

    groups: dict[str, list[str]] = defaultdict(list)
    for p in code:
        if p == anchor:
            continue
        parts = PurePosixPath(p).parts
        groups[parts[0] if len(parts) > 1 else "."].append(p)

    walk = [anchor]
    reasons = {anchor: anchor_reason}
    for d in sorted(groups, key=lambda d: (-len(groups[d]), d)):
        face = min(
            groups[d],
            key=lambda p: (-pagerank.get(p, 0.0), len(PurePosixPath(p).parts), p),
        )
        n = len(groups[d])
        label = "the repository root" if d == "." else f"{d}/"
        count = f"{n} code files live here" if n != 1 else "1 code file lives here"
        reasons[face] = f"The face of {label} — {count}."
        walk.append(face)
    return walk, reasons


_FANOUT_GROUP_MIN = 3


def _import_groups(
    graph_builder: Any, edge_types: frozenset[str] = frozenset({"imports"})
) -> dict[str, list[list[str]]]:
    """Imports edges grouped per source by originating import statement.

    A resolver fan-out (Go/JVM package import → every file in the package)
    emits many edges that share one source and identical ``imported_names``
    — semantically ONE import relationship. Groups of
    ``>= _FANOUT_GROUP_MIN`` targets are treated as fan-outs; smaller
    groups stay one-edge-one-relationship (multi-ext probes, pairs).
    """
    keyed: dict[tuple[str, tuple[str, ...]], list[str]] = defaultdict(list)
    try:
        for src, dst, data in graph_builder.graph().edges(data=True):
            if not (isinstance(src, str) and isinstance(dst, str)):
                continue
            if data.get("edge_type", "imports") not in edge_types:
                continue
            # Stdlib/external imports say nothing about where a walk can
            # go — only repo-internal relationships count.
            if src.startswith("external:") or dst.startswith("external:"):
                continue
            names = tuple(sorted(data.get("imported_names") or ())) or (dst,)
            keyed[(src, names)].append(dst)
    except Exception:  # pragma: no cover - defensive
        return {}
    groups: dict[str, list[list[str]]] = defaultdict(list)
    for (src, _names), targets in keyed.items():
        groups[src].append(targets)
    return groups


# The harness signal is "this test file *depends on* that one" — type
# references and inheritance (a base test class) are exactly that evidence;
# raw-graph type_use/heritage edges surface as plain imports in the export.
_DEPENDENCY_EDGE_TYPES = frozenset({"imports", "type_use", "heritage"})


def _import_pairs_excluding_fanout(graph_builder: Any) -> list[tuple[str, str]]:
    """``(src, dst)`` dependency pairs with fan-out groups dropped."""
    pairs: list[tuple[str, str]] = []
    for src, target_groups in _import_groups(
        graph_builder, edge_types=_DEPENDENCY_EDGE_TYPES
    ).items():
        for targets in target_groups:
            if len(targets) >= _FANOUT_GROUP_MIN:
                continue
            pairs.extend((src, dst) for dst in targets)
    return pairs


def _anchor_fanout_rank(graph_builder: Any) -> dict[str, int]:
    """Per-file count of distinct import *relationships* (fan-outs = 1).

    The walk's anchor claims "its imports fan out the widest" — that must
    mean import statements, not resolver edge multiplicity, or one Go
    package import (15 sibling edges) out-ranks a file with a dozen real
    dependencies and the anchor lands alphabetically-by-luck.
    """
    return {
        src: len(target_groups)
        for src, target_groups in _import_groups(graph_builder).items()
    }


def _curate_tour(
    kg: KnowledgeGraphResult, parsed_files: list[Any], graph_builder: Any
) -> list[dict] | None:
    """Build one canonical, execution-flow tour over the curated layers.

    Keeps the deterministic :func:`build_tour` ordering — README/overview
    first, then the entry points and their import neighbourhood walking inward
    (BFS depth) — so the tour follows how the program actually runs, not an
    abstract stack walk. Layer coverage is preserved by *swapping* redundant
    same-layer stops for representatives of uncovered runtime layers, never by
    re-sorting the walk. Adjacent layers (tests) take no walk slots: the suite
    gets a single closing stop before infrastructure. Step reasons state
    evidence (entry point, import depth, layer anchor), not stack position.
    Every step carries a ``layer_id`` mapping it to a curated layer; the LLM
    may later rewrite step *prose* only.
    """
    file_nodes = _file_nodes(kg)
    if not file_nodes:
        return None

    paths = [n["filePath"] for n in file_nodes]
    type_by_path = {n["filePath"]: n.get("type", "file") for n in file_nodes}
    lang_by_path = {n["filePath"]: (n.get("language") or "").lower() for n in file_nodes}
    code_langs = [
        lang
        for p, lang in lang_by_path.items()
        if lang and type_by_path.get(p) not in {"config", "document"}
    ]
    dominant_lang = _dominant_language(code_langs)
    # How much may the tour honestly claim? Exported additively so
    # consumers (UI, harness) can see the degradation level.
    graph_mode = _graph_mode(dominant_lang, lang_by_path, graph_builder)
    kg.project["graph_mode"] = graph_mode
    file_layers = {p: infer_layer(p, lang_by_path.get(p)) for p in paths}
    order = compute_layer_order(file_layers, _file_import_edges(graph_builder))

    pagerank = graph_builder.pagerank() or {}
    rank = {path: s for s, path in score_entry_points(parsed_files, pagerank)}
    barrels = {
        pf.file_info.path
        for pf in parsed_files
        if getattr(pf, "file_info", None) and _is_barrel(pf)
    }

    # Infra files (Docker/CI/etc.) close the tour; everything else is code.
    infra_paths = [p for p in paths if type_by_path.get(p) in {"service", "pipeline"}]

    # The overview step retargets to the root README — keep that file out of
    # the walk so the tour never visits it twice. Tests and example programs
    # are excluded from the walk universe *before* build_tour spends its step
    # budget; otherwise a samples-heavy repo (express) fills the budget with
    # stops that get filtered away afterwards.
    readme = _readme_overview_node(kg)
    overview_target = readme["filePath"] if readme is not None else None
    walk_universe = [
        p
        for p in paths
        if p != overview_target
        and file_layers.get(p) not in ADJACENT_LAYERS
        and not is_support_path(p)
        and not PurePosixPath(p).parts[0].startswith(".")  # dot-dir tooling
    ]

    project_name = kg.project.get("name") or "repository"
    # In structural mode the BFS walk is withheld entirely (a fake flow over
    # a near-edgeless graph is a lie); build_tour still selects the overview
    # and infra stops.
    base = build_tour(
        parsed_files,
        pagerank,
        _file_import_edges(graph_builder),
        file_page_paths=[] if graph_mode == "structural" else walk_universe,
        infra_paths=infra_paths,
        repo_name=project_name,
        max_stops=DEFAULT_MAX_STOPS,
        graph_mode=graph_mode,
        anchor_rank=_anchor_fanout_rank(graph_builder),
    )

    overview = [s for s in base if s.kind == "overview"]
    infra = [s for s in base if s.kind == "infra"]
    base_code = {s.target_path: s for s in base if s.kind == "code"}
    if not overview:
        overview_target = None

    by_layer: dict[str, list[str]] = defaultdict(list)
    for p in paths:
        by_layer[file_layers[p]].append(p)

    # One closing stop per adjacent layer present (the test suite) — tests
    # verify the system, they don't start it, so they never lead the walk.
    # Face = the shallowest suite anchor when present (conftest /
    # spec_helper / test_helper — registry-declared suite roots), else the
    # best code file, else anything (never a stray Cargo.toml if avoidable).
    #
    # Shared test harness files — base classes and helpers imported by two
    # or more other test files (AbstractFileSystemTest, BaseTestCase,
    # SpecUtil) — are what the suite runs ON, not where tests start. Their
    # heavy in-degree otherwise wins the pagerank tie-break on every repo
    # with shared fixtures.
    adjacent_paths = {
        p for layer in ADJACENT_LAYERS for p in by_layer.get(layer, [])
    }
    # Fan-out groups (one import statement expanded to many sibling targets
    # — Go/JVM package imports) are *not* evidence that a specific file is
    # referenced: a chi root test "imports" every sibling test through the
    # package fan-out. Only single-target import evidence counts here.
    harness_in: Counter[str] = Counter()
    for src, dst in _import_pairs_excluding_fanout(graph_builder):
        if src != dst and src in adjacent_paths and dst in adjacent_paths:
            harness_in[dst] += 1
    closing_paths: list[str] = []
    for layer in order:
        cands = by_layer.get(layer)
        if layer not in ADJACENT_LAYERS or not cands:
            continue
        anchors = sorted(
            (p for p in cands if PurePosixPath(p).stem.lower() in _SUITE_ANCHOR_STEMS),
            key=lambda p: (len(PurePosixPath(p).parts), p),
        )
        if anchors:
            closing_paths.append(anchors[0])
            continue
        code_cands = [
            p
            for p in cands
            if type_by_path.get(p) not in {"config", "document"}
            # Declaration descriptors (module-info.java) are source files
            # that describe a module, not tests — gson's shallow JPMS
            # descriptor must never face the suite.
            and PurePosixPath(p).name not in _DESCRIPTOR_FILENAMES
            # Fixture-shaped files (FooFixtures.java) hold test data; the
            # suite's face must be something that verifies behavior.
            and not _is_fixture_shaped(p)
        ]
        # Drop harness files unless that would leave nothing. One
        # single-target import from another test file is already harness
        # evidence — leaf tests have zero (okio's CipherFactory.kt had
        # exactly one cipher-test importer and still faced the suite).
        non_harness = [p for p in code_cands if harness_in.get(p, 0) < 1]
        if non_harness:
            code_cands = non_harness
        # When the repo declares test *projects* (.NET's Foo.Tests/ or
        # Foo.Specs/ sibling-project convention), the suite lives there —
        # a test/Shared/ helper dir next to them is auxiliary compile-time
        # plumbing, not where a maintainer says tests start.
        in_test_project = [
            p
            for p in code_cands
            if any(
                seg.endswith(_TEST_PROJECT_DIR_SUFFIXES) and len(seg) > 1
                for seg in PurePosixPath(p).parts[:-1]
            )
        ]
        if in_test_project:
            code_cands = in_test_project
        if code_cands:
            # No suite anchor (non-pytest/rspec suites): prefer the repo's
            # dominant language (gson's suite face is a .java, not a stray
            # .proto), then the shallowest test-root file (django's
            # tests/runtests.py), most-imported as the tie-break.
            code_cands.sort(
                key=lambda p: (
                    lang_by_path.get(p, "") != dominant_lang,
                    len(PurePosixPath(p).parts),
                    -pagerank.get(p, 0.0),
                    p,
                )
            )
            closing_paths.append(code_cands[0])
        else:
            closing_paths.append(_best_in_layer(cands, rank, pagerank))

    budget = max(0, DEFAULT_MAX_STOPS - len(overview) - len(closing_paths) - len(infra))
    swapped_depth: dict[str, int] = {}  # rep path -> depth of the slot it fills
    structural_reasons: dict[str, str] = {}

    if graph_mode == "structural":
        # Structure, not flow: evidence-ranked anchor + one face per
        # top-level code area. No layer-coverage swaps — the directory walk
        # IS the diversity, and "most depended-on" claims need edges.
        walk, structural_reasons = _structural_walk(
            walk_universe,
            type_by_path,
            dominant_lang,
            pagerank,
            graph_builder,
            project_name=project_name,
        )
        walk = walk[:budget]
    else:
        # The walk = build_tour's execution order minus adjacent-layer stops
        # and example programs (documentation-by-code, not the system),
        # truncated up front so later swaps land inside the kept window.
        walk = [
            s.target_path
            for s in base
            if s.kind == "code"
            and s.target_path != overview_target
            and file_layers.get(s.target_path) not in ADJACENT_LAYERS
            and not is_support_path(s.target_path)
        ]
        walk = walk[:budget]

        # --- Diversify for layer coverage (swap slots, never re-sort) -----
        seen_layers: set[str] = set()
        redundant_positions: list[int] = []
        for i, p in enumerate(walk):
            layer = file_layers.get(p)
            if layer in seen_layers:
                redundant_positions.append(i)
            else:
                seen_layers.add(layer)

        uncovered = [
            name for name in order if name not in seen_layers and name not in ADJACENT_LAYERS
        ]
        for layer in uncovered:
            if not redundant_positions:
                break
            # Manifests (mix.exs, project.clj, Setup.lhs) are code-shaped
            # but describe the project rather than implement it — never a
            # layer's face, same rule as the structural anchor.
            manifest_names = _LANG_REGISTRY.manifest_filenames()
            candidates = [
                p
                for p in by_layer.get(layer, [])
                if p not in walk
                and p != overview_target
                and not is_support_path(p)
                and not PurePosixPath(p).parts[0].startswith(".")  # never a layer face
                and PurePosixPath(p).name not in manifest_names
            ]
            if not candidates:
                continue
            # A layer's face must be code. A layer holding only configs/docs
            # (a plugins/ dir of JSON manifests) gets no manufactured stop —
            # except Config itself, where "this is where configuration
            # lives" is the point.
            # Infra-language scripts (run-hlint.sh, deploy.sh) wire the
            # project, they don't implement a layer — never its face.
            infra_langs = _LANG_REGISTRY.infra_languages()
            code_candidates = [
                p
                for p in candidates
                if type_by_path.get(p) not in {"config", "document"}
                and lang_by_path.get(p) not in infra_langs
            ]
            if not code_candidates and layer != "Config":
                continue
            rep = _best_in_layer(code_candidates or candidates, rank, pagerank)
            pos = redundant_positions.pop()
            replaced = base_code.get(walk[pos])
            swapped_depth[rep] = replaced.depth if replaced is not None else 0
            walk[pos] = rep
            seen_layers.add(layer)

    # --- Assemble the exported tour --------------------------------------
    tour: list[dict] = []
    order_n = 0

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

    max_depth = 0
    for p in walk:
        order_n += 1
        layer = file_layers.get(p, "")
        step = base_code.get(p)
        if p in structural_reasons:
            depth = 0  # import depth is meaningless without an import graph
            reason = structural_reasons[p]
        elif p in swapped_depth:
            depth = swapped_depth[p]
            reason = f"The {layer} layer's anchor — its most depended-on file."
        elif step is not None:
            depth = step.depth
            reason = step.reason
        else:  # pragma: no cover - walk paths come from base or swaps
            depth = 0
            reason = f"A key {layer} file on the walk from the entry points."
        if p in barrels:
            # A re-export shell may seed the walk (imports genuinely fan out
            # from it), but it must not claim to be an execution entry point.
            reason = "A re-export hub — the package's public surface fans out from here."
        max_depth = max(max_depth, depth)
        tour.append(
            {
                "order": order_n,
                "target_path": p,
                "page_type": "file_page",
                "title": PurePosixPath(p).name,
                "depth": depth,
                "kind": "code",
                "reason": reason,
                "layer_id": f"layer:{_slugify(layer)}",
            }
        )

    # Polyglot fairness: languages holding ≥20% of the code with
    # their own test files get named in the closing-stop reason — the stop
    # faces the dominant suite, but the others must not vanish.
    lang_counts = Counter(code_langs)
    total_code = sum(lang_counts.values()) or 1
    test_langs = {
        lang_by_path.get(p, "")
        for layer in ADJACENT_LAYERS
        for p in by_layer.get(layer, [])
    }
    other_suites = sorted(
        spec.display_name
        for tag, n in lang_counts.items()
        if tag != dominant_lang
        and n / total_code >= 0.20
        and tag in test_langs
        and (spec := _LANG_REGISTRY.get(tag)) is not None
    )
    closing_reason = "The test suite — how the system's behavior is verified."
    if other_suites:
        closing_reason = (
            "The test suite — how the system's behavior is verified "
            f"(the {' and '.join(other_suites)} test suite"
            f"{'s' if len(other_suites) > 1 else ''} live"
            f"{'' if len(other_suites) > 1 else 's'} alongside it)."
        )

    for p in closing_paths:
        order_n += 1
        layer = file_layers.get(p, "Test")
        max_depth += 1
        tour.append(
            {
                "order": order_n,
                "target_path": p,
                "page_type": "file_page",
                "title": PurePosixPath(p).name,
                "depth": max_depth,
                "kind": "code",
                "reason": closing_reason,
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
    if node_type == "pipeline" or "ci" in tags:
        return f"CI / pipeline definition: {PurePosixPath(path).name}."
    if node_type == "service" or "infra" in tags:
        return f"Infrastructure definition: {PurePosixPath(path).name}."
    if node_type == "schema" or "data" in tags:
        return f"Data / schema definition: {PurePosixPath(path).name}."
    if node_type == "config" or "config" in tags:
        return f"Configuration file: {PurePosixPath(path).name}."
    if node_type == "document":
        return f"Documentation: {PurePosixPath(path).name}."
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
        "schema_version": data.get("version", "1.0.0"),
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
