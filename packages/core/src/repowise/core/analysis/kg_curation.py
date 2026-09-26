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
from collections import defaultdict
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any

from repowise.core.analysis.kg_inputs import (
    _SUBSTANTIVE_KINDS,
    _dominant_language,  # noqa: F401  (re-exported; tests import it from here)
    _file_import_edges,
    _file_nodes,
    _is_barrel,
)
from repowise.core.analysis.kg_modules import (  # noqa: F401  (re-exported)
    GENERIC_ORG_SEGMENTS,
    _common_dir_prefix,
    derive_modules,
    dominant_segments,
)
from repowise.core.analysis.kg_tour import (  # noqa: F401  (re-exported)
    _anchor_fanout_rank,
    _curate_tour,
    _import_pairs_excluding_fanout,
)
from repowise.core.analysis.kg_validation import (
    _MAX_ENTRY_POINTS,
    _MAX_LAYERS,
    KGValidation,
    build_portable_kg,
    validate_kg,
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
from repowise.core.generation.tour import score_entry_points
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
    # export.
    layers = _guarded(
        "kg_curation._curate_layers failed; keeping community layers",
        _curate_layers,
        kg,
        graph_builder,
    )
    if layers is not None:
        kg.layers = layers
        # Wiki modules are a *sibling* artifact of the curated layers (same
        # splitting machinery, module-sized granularity). Only derived when the
        # spine landed — community layers would make the dir-split meaningless,
        # and downstream consumers fall back to community grouping when this
        # stays empty (the fallback matrix's "degraded" row).
        modules = _guarded(
            "kg_curation._curate_modules failed; exporting no modules", _curate_modules, kg
        )
        if modules is not None:
            kg.modules = modules

    _guarded(
        "kg_curation._curate_entry_points failed; keeping raw entry points",
        _curate_entry_points,
        kg,
        parsed_files,
        graph_builder,
    )

    tour = _guarded(
        "kg_curation._curate_tour failed; keeping existing tour",
        _curate_tour,
        kg,
        parsed_files,
        graph_builder,
        hotspot_commits=_hotspot_commits(git_meta_map),
    )
    if tour is not None:
        kg.tour = tour

    _guarded("kg_curation._curate_node_types failed; keeping coarse types", _curate_node_types, kg)

    if not defer_summary_floor:
        _guarded(
            "kg_curation summary floor failed; leaving summaries empty",
            apply_summary_floor,
            kg,
            parsed_files,
        )

    return kg


def _guarded(failure: str, step: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run one curation *step*; on error log *failure* and return ``None``."""
    try:
        return step(*args, **kwargs)
    except Exception:  # pragma: no cover - defensive; keep the uncurated field
        logger.exception(failure)
        return None


def _hotspot_commits(git_meta_map: dict[str, dict] | None) -> dict[str, int]:
    """Recent commit counts of the flagged churn hotspots.

    Genuine churn hotspots (a constantly-edited file off the hot import path)
    earn a tour stop on this signal alone. Only flagged hotspots with recent
    commits qualify; repos without git history pass an empty map and the tour
    is unchanged.
    """
    return {
        path: int(meta.get("commit_count_90d", 0) or 0)
        for path, meta in (git_meta_map or {}).items()
        if meta.get("is_hotspot")
    }


# ---------------------------------------------------------------------------
# Phase 1 — curated layers (replace raw-community layers with the spine)
# ---------------------------------------------------------------------------


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

    layers = [
        _layer_record(layer_name, display_order, by_layer[layer_name], order_basis, id_to_path)
        for display_order, layer_name in enumerate(order)
    ]
    return layers if _layers_hold_invariants(layers, len(file_nodes)) else None


def _layer_record(
    layer_name: str,
    display_order: int,
    node_ids: list[str],
    order_basis: str,
    id_to_path: dict[str, str],
) -> dict[str, Any]:
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
    return layer


def _layers_hold_invariants(layers: list[dict], file_count: int) -> bool:
    """Degrade rather than ship a broken artifact: enforce bound + partition."""
    total = sum(len(layer["nodeIds"]) for layer in layers)
    if not layers or len(layers) > _MAX_LAYERS or total != file_count:
        logger.warning(
            "kg_curation: curated layers failed invariant "
            "(count=%d, partition=%d/%d); keeping community layers",
            len(layers),
            total,
            file_count,
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Wiki modules — right-sized directory groups derived from the curated layers
# ---------------------------------------------------------------------------


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
    if not modules or not _modules_hold_invariants(modules, id_to_path):
        return None
    return modules


def _modules_hold_invariants(modules: list[dict], id_to_path: dict[str, str]) -> bool:
    seen: set[str] = set()
    for m in modules:
        for nid in m["nodeIds"]:
            if nid in seen or nid not in id_to_path:
                logger.warning(
                    "kg_curation: derived modules failed partition invariant; "
                    "exporting no modules"
                )
                return False
            seen.add(nid)
    names = [m["name"] for m in modules]
    ids = [m["id"] for m in modules]
    if len(set(names)) != len(names) or len(set(ids)) != len(ids):
        logger.warning(
            "kg_curation: derived module names/ids not unique; exporting no modules"
        )
        return False
    return True


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
    betweenness = _betweenness(graph_builder)

    paths = _flagged_entry_paths(kg, pf_by_path)
    if not paths:
        # No ingestion-flagged entries (or all were barrels): fall back to the
        # strong filename scorers the tour seeds from (score >= 3 means an
        # entry-style name or flag, never just shallow/high-PageRank).
        paths = [
            path
            for s, path in score_entry_points(parsed_files, pagerank)
            if s >= 3.0 and _is_entry_candidate(path, lang_by_path.get(path, ""), pf_by_path)
        ]
    candidates = [(path, pagerank.get(path, 0.0), betweenness.get(path, 0.0)) for path in paths]

    ranked = rank_entry_points(candidates, conventional_entry_stems())
    kg.project["entry_points"] = ranked[:_MAX_ENTRY_POINTS]
    kg.project["entry_candidates"] = ranked


def _betweenness(graph_builder: Any) -> dict[str, float]:
    try:
        return graph_builder.betweenness_centrality() or {}
    except Exception:  # pragma: no cover - defensive
        return {}


def _flagged_entry_paths(kg: KnowledgeGraphResult, pf_by_path: dict[str, Any]) -> list[str]:
    """Ingestion-flagged entry files that survive candidacy, in node order.

    A flagged re-export barrel is retagged ``barrel`` in the presentation view
    (the AST graph's flag is untouched) and dropped.
    """
    paths: list[str] = []
    for node in kg.nodes:
        if not _is_flagged_file_node(node):
            continue
        path = node.get("filePath", "")
        language = (node.get("language") or "").lower()
        if _off_the_entry_path(path, language):
            continue
        if _is_barrel_path(path, pf_by_path):
            _retag_as_barrel(node)
            continue
        if not not_an_execution_start(path, language):
            paths.append(path)
    return paths


def _is_flagged_file_node(node: dict) -> bool:
    nid = node.get("id", "")
    if not (isinstance(nid, str) and nid.startswith("file:")):
        return False
    return "entry_point" in (node.get("tags") or [])


def _retag_as_barrel(node: dict) -> None:
    new_tags = [t for t in (node.get("tags") or []) if t != "entry_point"]
    if "barrel" not in new_tags:
        new_tags.append("barrel")
    node["tags"] = new_tags


def _is_barrel_path(path: str, pf_by_path: dict[str, Any]) -> bool:
    pf = pf_by_path.get(path)
    return pf is not None and _is_barrel(pf)


def _off_the_entry_path(path: str, language: str) -> bool:
    # Test fixtures (a wsgi.py inside tests/) and sample programs
    # (examples/*/main.go) may carry the ingestion flag, but they are
    # not where a reader enters the system.
    return infer_layer(path, language) in ADJACENT_LAYERS or is_support_path(path)


def _is_entry_candidate(path: str, language: str, pf_by_path: dict[str, Any]) -> bool:
    """Whether a scored (unflagged) file may stand as an entry point."""
    if _off_the_entry_path(path, language) or _is_barrel_path(path, pf_by_path):
        return False
    return not not_an_execution_start(path, language)


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

    if not is_code and _is_ci_path(p, name):
        return "pipeline", "ci"
    if (not is_code and _is_infra_path(p, name)) or suffix in _INFRA_SUFFIXES:
        return "service", "infra"
    if any(m in p for m in _DATA_PATH_MARKERS) or suffix in _DATA_SUFFIXES:
        return "schema", "data"
    return current_type, None


def _is_ci_path(lowered_path: str, name: str) -> bool:
    return any(m in lowered_path for m in _CI_PATH_MARKERS) or name == "jenkinsfile"


def _is_infra_path(lowered_path: str, name: str) -> bool:
    if name.startswith("dockerfile") or any(m in name for m in _INFRA_NAME_MARKERS):
        return True
    return any(m in lowered_path for m in _INFRA_PATH_MARKERS)


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


# Support-file summary templates in precedence order: the first row whose
# presentation type or tag matches the node names it.
_SUPPORT_TEMPLATES: tuple[tuple[str, str | None, str], ...] = (
    ("pipeline", "ci", "CI / pipeline definition: {name}."),
    ("service", "infra", "Infrastructure definition: {name}."),
    ("schema", "data", "Data / schema definition: {name}."),
    ("config", "config", "Configuration file: {name}."),
    ("document", None, "Documentation: {name}."),
)


def _cheap_summary(node: dict, parsed_file: Any | None) -> str:
    """A deterministic, honest fallback summary (zero LLM cost)."""
    path = node["filePath"]
    tags = node.get("tags") or []

    if "barrel" in tags:
        return f"Re-export barrel for {PurePosixPath(path).parent.name or 'root'}/."
    support = _support_summary(path, node.get("type", "file"), tags)
    if support is not None:
        return support
    if "test" in tags:
        return f"Tests for {_infer_test_target(path)}."
    return _code_summary(node, parsed_file)


def _support_summary(path: str, node_type: str, tags: list[str]) -> str | None:
    template = next(
        (text for kind, tag, text in _SUPPORT_TEMPLATES if node_type == kind or tag in tags),
        None,
    )
    if template is None:
        return None
    # Recognised scaffolding earns a real role instead of a bare name
    # restatement; only genuinely opaque support files fall back to the
    # type template.
    role = well_known_role(path)
    if role is not None:
        return role
    return template.format(name=PurePosixPath(path).name)


def _code_summary(node: dict, parsed_file: Any | None) -> str:
    """Name the code file's layer and its most prominent symbols."""
    path = node["filePath"]
    stem = PurePosixPath(path).stem
    layer = infer_layer(path, (node.get("language") or "").lower())
    symbol_names = _top_symbol_names(parsed_file)
    if symbol_names:
        return f"{layer} module {stem} defining {', '.join(symbol_names)}."
    count = node.get("symbolCount", 0)
    if count:
        return f"{layer} module {stem} ({count} symbols)."
    return f"{layer} module {stem}."


def _top_symbol_names(parsed_file: Any | None) -> list[str]:
    if parsed_file is None:
        return []
    return [
        getattr(s, "name", "")
        for s in (getattr(parsed_file, "symbols", []) or [])
        if getattr(s, "kind", "") in _SUBSTANTIVE_KINDS and getattr(s, "name", "")
    ][:3]


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
