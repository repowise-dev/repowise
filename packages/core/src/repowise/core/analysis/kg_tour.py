"""The curated knowledge graph's canonical tour (curation phase 3).

An execution-flow walk over the curated layers, or a structural walk when the
import graph cannot support flow claims.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from repowise.core.analysis.kg_inputs import (
    _dominant_language,
    _file_import_edges,
    _file_nodes,
    _is_barrel,
)
from repowise.core.analysis.knowledge_graph import KnowledgeGraphResult, _slugify
from repowise.core.generation.layers import ADJACENT_LAYERS, compute_layer_order, infer_layer
from repowise.core.generation.tour import (
    DEFAULT_MAX_STOPS,
    build_tour,
    score_entry_points,
    select_hotspot_stop,
)
from repowise.core.ids import is_external
from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY
from repowise.core.support_paths import is_support_path

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
            # "tested_by" was a second member here; it is a knowledge-graph
            # export label, never a raw edge type, so it never matched.
            if (data or {}).get("edge_type") == "imports" and src in dom_files:
                edge_count += 1
                if isinstance(dst, str) and is_external(dst):
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
            if is_external(src) or is_external(dst):
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
# Deliberately narrower than FILE_DEPENDENCY_EDGE_TYPES: framework and dynamic
# wiring is not harness evidence. "heritage" used to be a third member and was
# never an edge type — inheritance reaches the graph as extends/implements.
_DEPENDENCY_EDGE_TYPES = frozenset({"imports", "type_use"})


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


def _drop_extra_barrels(paths: list[str], barrels: set[str], keep: int = 1) -> list[str]:
    """Return *paths* with all but the first *keep* re-export barrels removed.

    A barrel (``index.ts`` / ``__init__.py``) re-exports a package's public
    surface; one such stop orients a reader, but five identical "re-export hub"
    stops are noise that crowds out real code. *paths* is in execution/BFS-depth
    order, so the first barrel kept is the shallowest. Dropping the rest before
    the budget truncation lets real code files beyond the budget slide up.
    """
    out: list[str] = []
    kept = 0
    for p in paths:
        if p in barrels:
            if kept >= keep:
                continue
            kept += 1
        out.append(p)
    return out


def _curate_tour(
    kg: KnowledgeGraphResult,
    parsed_files: list[Any],
    graph_builder: Any,
    hotspot_commits: dict[str, int] | None = None,
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

    ctx = _TourContext.build(kg, file_nodes, parsed_files, graph_builder)
    # How much may the tour honestly claim? Exported additively so
    # consumers (UI, harness) can see the degradation level.
    kg.project["graph_mode"] = ctx.graph_mode

    # The README is the overview stop, so it stays out of the walk. Tests and
    # examples are excluded before build_tour spends its step budget on them.
    readme = _readme_overview_node(kg)
    overview_target = readme["filePath"] if readme is not None else None
    walk_universe = [
        p
        for p in ctx.paths
        if p != overview_target
        and ctx.file_layers.get(p) not in ADJACENT_LAYERS
        and not is_support_path(p)
        and not PurePosixPath(p).parts[0].startswith(".")  # dot-dir tooling
    ]

    project_name = kg.project.get("name") or "repository"
    # In structural mode the BFS walk is withheld entirely (a fake flow over
    # a near-edgeless graph is a lie); build_tour still selects the overview
    # and infra stops.
    base = build_tour(
        parsed_files,
        ctx.pagerank,
        _file_import_edges(graph_builder),
        file_page_paths=[] if ctx.graph_mode == "structural" else walk_universe,
        # Infra files (Docker/CI/etc.) close the tour; everything else is code.
        infra_paths=[p for p in ctx.paths if ctx.type_by_path.get(p) in {"service", "pipeline"}],
        repo_name=project_name,
        max_stops=DEFAULT_MAX_STOPS,
        graph_mode=ctx.graph_mode,
        anchor_rank=_anchor_fanout_rank(graph_builder),
    )

    overview = [s for s in base if s.kind == "overview"]
    infra = [s for s in base if s.kind == "infra"]
    base_code = {s.target_path: s for s in base if s.kind == "code"}
    if not overview:
        overview_target = None

    closing_paths = _closing_stop_paths(ctx, graph_builder)
    budget = max(0, DEFAULT_MAX_STOPS - len(overview) - len(closing_paths) - len(infra))

    if ctx.graph_mode == "structural":
        # Structure, not flow: evidence-ranked anchor + one face per
        # top-level code area. No layer-coverage swaps — the directory walk
        # IS the diversity, and "most depended-on" claims need edges.
        walk, structural_reasons = _structural_walk(
            walk_universe,
            ctx.type_by_path,
            ctx.dominant_lang,
            ctx.pagerank,
            graph_builder,
            project_name=project_name,
        )
        walk = walk[:budget]
        flow = _FlowWalk(walk=walk)
    else:
        structural_reasons = {}
        flow = _flow_walk(
            ctx, base, base_code, walk_universe, overview_target, budget, hotspot_commits
        )

    # --- Assemble the exported tour --------------------------------------
    tour: list[dict] = []
    if overview:
        tour.append(_overview_step(overview[0], readme, ctx.file_layers))
    walk_steps, max_depth = _walk_steps(
        flow, structural_reasons, base_code, ctx, first_order=len(tour) + 1
    )
    tour.extend(walk_steps)
    tour.extend(
        _closing_steps(closing_paths, ctx, first_order=len(tour) + 1, depth_after=max_depth)
    )
    for s in infra:
        step = s.as_dict()
        step["order"] = len(tour) + 1
        step["layer_id"] = f"layer:{_slugify(ctx.file_layers.get(s.target_path, 'Config'))}"
        tour.append(step)
    return tour


@dataclass
class _TourContext:
    """Per-file facts every stage of the tour reads."""

    paths: list[str]
    type_by_path: dict[str, str]
    lang_by_path: dict[str, str]
    code_langs: list[str]
    dominant_lang: str
    graph_mode: str
    file_layers: dict[str, str]
    order: list[str]
    pagerank: dict[str, float]
    rank: dict[str, float]
    barrels: set[str]
    by_layer: dict[str, list[str]]

    @classmethod
    def build(
        cls,
        kg: KnowledgeGraphResult,
        file_nodes: list[dict],
        parsed_files: list[Any],
        graph_builder: Any,
    ) -> _TourContext:
        paths = [n["filePath"] for n in file_nodes]
        type_by_path = {n["filePath"]: n.get("type", "file") for n in file_nodes}
        lang_by_path = {n["filePath"]: (n.get("language") or "").lower() for n in file_nodes}
        code_langs = [
            lang
            for p, lang in lang_by_path.items()
            if lang and type_by_path.get(p) not in {"config", "document"}
        ]
        dominant_lang = _dominant_language(code_langs)
        graph_mode = _graph_mode(dominant_lang, lang_by_path, graph_builder)
        file_layers = {p: infer_layer(p, lang_by_path.get(p)) for p in paths}
        order = compute_layer_order(file_layers, _file_import_edges(graph_builder))

        pagerank = graph_builder.pagerank() or {}
        rank = {path: s for s, path in score_entry_points(parsed_files, pagerank)}
        barrels = {
            pf.file_info.path
            for pf in parsed_files
            if getattr(pf, "file_info", None) and _is_barrel(pf)
        }
        by_layer: dict[str, list[str]] = defaultdict(list)
        for p in paths:
            by_layer[file_layers[p]].append(p)
        return cls(
            paths=paths,
            type_by_path=type_by_path,
            lang_by_path=lang_by_path,
            code_langs=code_langs,
            dominant_lang=dominant_lang,
            graph_mode=graph_mode,
            file_layers=file_layers,
            order=order,
            pagerank=pagerank,
            rank=rank,
            barrels=barrels,
            by_layer=by_layer,
        )


def _closing_stop_paths(ctx: _TourContext, graph_builder: Any) -> list[str]:
    """One closing stop per adjacent layer present (the test suite).

    Tests verify the system rather than start it, so they close the tour.
    Shared harness files (imported by other test files) never face the suite.
    """
    adjacent_paths = {
        p for layer in ADJACENT_LAYERS for p in ctx.by_layer.get(layer, [])
    }
    # A package-import fan-out is not evidence that a specific file is
    # referenced, so only single-target imports count as harness evidence.
    harness_in: Counter[str] = Counter()
    for src, dst in _import_pairs_excluding_fanout(graph_builder):
        if src != dst and src in adjacent_paths and dst in adjacent_paths:
            harness_in[dst] += 1
    closing_paths: list[str] = []
    for layer in ctx.order:
        cands = ctx.by_layer.get(layer)
        if layer not in ADJACENT_LAYERS or not cands:
            continue
        closing_paths.append(_suite_face(cands, ctx, harness_in))
    return closing_paths


def _suite_face(cands: list[str], ctx: _TourContext, harness_in: Counter[str]) -> str:
    """The file that stands for one adjacent layer's suite in the tour.

    A registry-declared suite anchor wins; else the best non-harness,
    non-fixture code file; else the layer's best file of any kind.
    """
    anchors = sorted(
        (p for p in cands if PurePosixPath(p).stem.lower() in _SUITE_ANCHOR_STEMS),
        key=lambda p: (len(PurePosixPath(p).parts), p),
    )
    if anchors:
        return anchors[0]
    code_cands = [
        p
        for p in cands
        if ctx.type_by_path.get(p) not in {"config", "document"}
        # Declaration descriptors describe a module; fixtures hold test data.
        and PurePosixPath(p).name not in _DESCRIPTOR_FILENAMES
        and not _is_fixture_shaped(p)
    ]
    # Drop harness files unless that would leave nothing: one single-target
    # import from another test file is already harness evidence.
    non_harness = [p for p in code_cands if harness_in.get(p, 0) < 1]
    if non_harness:
        code_cands = non_harness
    # When the repo declares test projects (Foo.Tests/), the suite lives there.
    in_test_project = [p for p in code_cands if _in_test_project(p)]
    if in_test_project:
        code_cands = in_test_project
    if not code_cands:
        return _best_in_layer(cands, ctx.rank, ctx.pagerank)
    # Prefer the dominant language, then the shallowest file, then PageRank.
    return min(
        code_cands,
        key=lambda p: (
            ctx.lang_by_path.get(p, "") != ctx.dominant_lang,
            len(PurePosixPath(p).parts),
            -ctx.pagerank.get(p, 0.0),
            p,
        ),
    )


def _in_test_project(path: str) -> bool:
    return any(
        seg.endswith(_TEST_PROJECT_DIR_SUFFIXES) and len(seg) > 1
        for seg in PurePosixPath(path).parts[:-1]
    )


@dataclass
class _FlowWalk:
    """The code stops of the tour and how each one got there."""

    walk: list[str]
    swapped_depth: dict[str, int] = field(default_factory=dict)  # rep -> depth of its slot
    hotspot_added: str | None = None  # churn hotspot given a reserved slot


def _flow_walk(
    ctx: _TourContext,
    base: list[Any],
    base_code: dict[str, Any],
    walk_universe: list[str],
    overview_target: str | None,
    budget: int,
    hotspot_commits: dict[str, int] | None,
) -> _FlowWalk:
    """build_tour's execution order, trimmed to the budget and diversified.

    The walk = build_tour's execution order minus adjacent-layer stops and
    example programs (documentation-by-code, not the system), truncated up
    front so later swaps land inside the kept window.
    """
    walk_all = [
        s.target_path
        for s in base
        if s.kind == "code"
        and s.target_path != overview_target
        and ctx.file_layers.get(s.target_path) not in ADJACENT_LAYERS
        and not is_support_path(s.target_path)
    ]
    # A churn hotspot off the import path earns one reserved slot, picked from
    # the whole code universe; without git history the map is empty.
    hotspot_path: str | None = None
    if hotspot_commits:
        hotspot_pool = [
            p
            for p in walk_universe
            if p not in ctx.barrels
            and ctx.file_layers.get(p) not in ADJACENT_LAYERS
            and ctx.type_by_path.get(p) not in {"config", "document"}
        ]
        hotspot_path = select_hotspot_stop(hotspot_pool, hotspot_commits)
    # One re-export barrel earns a stop; the rest are demoted so real code
    # fills the budget instead of a run of identical "public surface" hubs.
    code_order = _drop_extra_barrels(walk_all, ctx.barrels)
    reserve = 1 if (hotspot_path is not None and hotspot_path not in code_order[:budget]) else 0
    flow = _FlowWalk(walk=code_order[: max(0, budget - reserve)])

    _diversify_layers(flow, ctx, base_code, overview_target)

    # Spend the reserved slot last so layer-coverage swaps keep priority.
    if reserve and hotspot_path is not None and hotspot_path not in flow.walk:
        flow.walk.append(hotspot_path)
        flow.hotspot_added = hotspot_path
    return flow


def _diversify_layers(
    flow: _FlowWalk,
    ctx: _TourContext,
    base_code: dict[str, Any],
    overview_target: str | None,
) -> None:
    """Swap redundant same-layer stops for faces of uncovered layers.

    Swaps slots, never re-sorts, so the walk keeps its execution order.
    """
    walk = flow.walk
    seen_layers: set[str] = set()
    redundant_positions: list[int] = []
    for i, p in enumerate(walk):
        layer = ctx.file_layers.get(p)
        if layer in seen_layers:
            redundant_positions.append(i)
        else:
            seen_layers.add(layer)

    uncovered = [
        name for name in ctx.order if name not in seen_layers and name not in ADJACENT_LAYERS
    ]
    for layer in uncovered:
        if not redundant_positions:
            break
        rep = _layer_face(layer, ctx, walk, overview_target)
        if rep is None:
            continue
        pos = redundant_positions.pop()
        replaced = base_code.get(walk[pos])
        flow.swapped_depth[rep] = replaced.depth if replaced is not None else 0
        walk[pos] = rep
        seen_layers.add(layer)


def _layer_face(
    layer: str, ctx: _TourContext, walk: list[str], overview_target: str | None
) -> str | None:
    """The stop that represents *layer* in the walk, or None if it has no face."""
    # Manifests describe the project rather than implement it: never a face.
    manifest_names = _LANG_REGISTRY.manifest_filenames()
    candidates = [
        p
        for p in ctx.by_layer.get(layer, [])
        if p not in walk
        and p != overview_target
        and not is_support_path(p)
        and p not in ctx.barrels  # a re-export shell is never a layer's face
        and not PurePosixPath(p).parts[0].startswith(".")  # never a layer face
        and PurePosixPath(p).name not in manifest_names
    ]
    if not candidates:
        return None
    # A face must be code (not config, docs or infra scripts), except for the
    # Config layer itself.
    infra_langs = _LANG_REGISTRY.infra_languages()
    code_candidates = [
        p
        for p in candidates
        if ctx.type_by_path.get(p) not in {"config", "document"}
        and ctx.lang_by_path.get(p) not in infra_langs
    ]
    if not code_candidates and layer != "Config":
        return None
    return _best_in_layer(code_candidates or candidates, ctx.rank, ctx.pagerank)


def _overview_step(overview: Any, readme: dict | None, file_layers: dict[str, str]) -> dict:
    ov = overview.as_dict()
    ov["order"] = 1
    if readme is not None:
        ov["target_path"] = readme["filePath"]
        ov["title"] = PurePosixPath(readme["filePath"]).name
        ov["layer_id"] = f"layer:{_slugify(file_layers[readme['filePath']])}"
    else:
        ov["layer_id"] = None
    return ov


def _walk_steps(
    flow: _FlowWalk,
    structural_reasons: dict[str, str],
    base_code: dict[str, Any],
    ctx: _TourContext,
    *,
    first_order: int,
) -> tuple[list[dict], int]:
    """The walk's code steps, and the deepest import depth among them."""
    steps: list[dict] = []
    max_depth = 0
    for order_n, p in enumerate(flow.walk, start=first_order):
        layer = ctx.file_layers.get(p, "")
        depth, reason = _walk_step_basis(p, layer, flow, structural_reasons, base_code)
        if p in ctx.barrels:
            # A re-export shell may seed the walk (imports genuinely fan out
            # from it), but it must not claim to be an execution entry point.
            reason = "A re-export hub — the package's public surface fans out from here."
        max_depth = max(max_depth, depth)
        steps.append(
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
    return steps, max_depth


def _walk_step_basis(
    path: str,
    layer: str,
    flow: _FlowWalk,
    structural_reasons: dict[str, str],
    base_code: dict[str, Any],
) -> tuple[int, str]:
    """``(depth, reason)`` for one walk stop, by how it earned its place."""
    if path in structural_reasons:
        # Import depth is meaningless without an import graph.
        return 0, structural_reasons[path]
    if path in flow.swapped_depth:
        return flow.swapped_depth[path], f"The {layer} layer's anchor — its most depended-on file."
    if path == flow.hotspot_added:
        return 0, (
            "A top churn hotspot — one of the most frequently changed files "
            "in the repo; worth understanding early."
        )
    step = base_code.get(path)
    if step is None:  # pragma: no cover - walk paths come from base or swaps
        return 0, f"A key {layer} file on the walk from the entry points."
    return step.depth, step.reason


def _closing_steps(
    closing_paths: list[str], ctx: _TourContext, *, first_order: int, depth_after: int
) -> list[dict]:
    """The suite's closing stops, each one step deeper than the one before."""
    closing_reason = _closing_reason(ctx)
    return [
        {
            "order": first_order + i,
            "target_path": p,
            "page_type": "file_page",
            "title": PurePosixPath(p).name,
            "depth": depth_after + i + 1,
            "kind": "code",
            "reason": closing_reason,
            "layer_id": f"layer:{_slugify(ctx.file_layers.get(p, 'Test'))}",
        }
        for i, p in enumerate(closing_paths)
    ]


def _closing_reason(ctx: _TourContext) -> str:
    """The closing stops' reason, naming other sizeable languages' suites.

    Polyglot fairness: languages holding ≥20% of the code with their own test
    files get named in the closing-stop reason — the stop faces the dominant
    suite, but the others must not vanish.
    """
    lang_counts = Counter(ctx.code_langs)
    total_code = sum(lang_counts.values()) or 1
    test_langs = {
        ctx.lang_by_path.get(p, "")
        for layer in ADJACENT_LAYERS
        for p in ctx.by_layer.get(layer, [])
    }
    other_suites = sorted(
        spec.display_name
        for tag, n in lang_counts.items()
        if tag != ctx.dominant_lang
        and n / total_code >= 0.20
        and tag in test_langs
        and (spec := _LANG_REGISTRY.get(tag)) is not None
    )
    if not other_suites:
        return "The test suite — how the system's behavior is verified."
    return (
        "The test suite — how the system's behavior is verified "
        f"(the {' and '.join(other_suites)} test suite"
        f"{'s' if len(other_suites) > 1 else ''} live"
        f"{'' if len(other_suites) > 1 else 's'} alongside it)."
    )
