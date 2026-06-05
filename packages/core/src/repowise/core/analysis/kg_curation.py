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

Curation is feature-flagged (``REPOWISE_KG_CURATION``) and defaults **off** so
the exported KG is byte-identical to today's until the multi-repo validation
gate passes. With the flag off, :func:`curate_knowledge_graph` is a no-op that
returns its input unchanged.
"""

from __future__ import annotations

import logging
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from repowise.core.analysis.knowledge_graph import KnowledgeGraphResult, _slugify
from repowise.core.generation.layers import (
    ADJACENT_LAYERS,
    compute_layer_order,
    infer_layer,
    is_example_path,
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

# Honest-degradation thresholds. Density = (imports + tested_by)
# edges per dominant-language file — the same definition the validation
# harness uses, calibrated on the 13-repo matrix: express (1.89, broken CJS
# resolution) and sinatra (1.48, broken require resolution) land in
# "sparse"; every healthy repo sits at ≥ 2.2. Repos below the file floor
# skip the density check — density on a 7-file repo is noise, not evidence.
_FLOW_DENSITY_FLOOR = 2.0
_STRUCTURAL_DENSITY_FLOOR = 0.3
_MODE_MIN_FILES = 25


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
    try:
        for src, _dst, data in graph_builder.graph().edges(data=True):
            if (data or {}).get("edge_type") in ("imports", "tested_by") and src in dom_files:
                edge_count += 1
    except Exception:  # pragma: no cover - defensive
        return "flow" if support == "full" else "sparse"
    if len(dom_files) < _MODE_MIN_FILES:
        return "flow" if support == "full" else "sparse"
    density = edge_count / len(dom_files)
    if density < _STRUCTURAL_DENSITY_FLOOR:
        return "structural"
    if support == "partial" or density < _FLOW_DENSITY_FLOOR:
        return "sparse"
    return "flow"

__all__ = [
    "KGValidation",
    "apply_summary_floor",
    "build_portable_kg",
    "curate_knowledge_graph",
    "curation_enabled",
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
    few, ``project.entry_candidates`` the full ranked list. When ingestion
    flagged no entries at all, the strong :func:`score_entry_points` scorers
    (entry-style filenames) fill in, so the orientation panel never opens empty
    on repos without a detectable main.
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
        if infer_layer(path) in ADJACENT_LAYERS or is_example_path(path):
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
            if infer_layer(path) in ADJACENT_LAYERS or is_example_path(path):
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
    dominant_lang = Counter(code_langs).most_common(1)[0][0] if code_langs else ""
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
        and not is_example_path(p)
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
        ]
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
            and not is_example_path(s.target_path)
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
            candidates = [
                p
                for p in by_layer.get(layer, [])
                if p not in walk
                and p != overview_target
                and not is_example_path(p)
                and not PurePosixPath(p).parts[0].startswith(".")  # never a layer face
            ]
            if not candidates:
                continue
            # A layer's face must be code. A layer holding only configs/docs
            # (a plugins/ dir of JSON manifests) gets no manufactured stop —
            # except Config itself, where "this is where configuration
            # lives" is the point.
            code_candidates = [
                p for p in candidates if type_by_path.get(p) not in {"config", "document"}
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

    # -- Summaries ---------------------------------------------------------
    empty_summaries = [nid for nid, s in summary_by_id.items() if not s]
    if empty_summaries:
        errors.append(f"{len(empty_summaries)} file nodes have an empty summary")
    summary_completeness = 1.0 - len(empty_summaries) / file_count if file_count else 1.0

    metrics = {
        "file_count": file_count,
        "layer_count": n_layers,
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
        "entry_point_count": validation.metrics.get("entry_point_count", 0),
        "tour_steps": validation.metrics.get("tour_steps", 0),
        "validation": validation.as_dict(),
    }
    return data, validation
