"""Wiki modules: right-sized directory groups derived from the curated KG layers."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import PurePosixPath

from repowise.core.analysis.knowledge_graph import _slugify


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
