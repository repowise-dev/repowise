"""Pure checks over an exported knowledge-graph.json dict.

Two layers, both side-effect free so they unit-test on synthetic dicts:

- :func:`compute_stats` — per-language graph-quality numbers (edge density,
  resolution rate, orphan ratio). These are the canary metrics: express's
  CommonJS re-export gap showed up as 1.7 imports/file vs chi's 9.7 long
  before any tour smell fired.
- :func:`run_smells` — the smell suite. Ports the original 7 checks from the
  throwaway /tmp inspect.py and adds graph-level ones (edgeless graphs,
  density regression vs a stored baseline, catch-all layers, entry-point
  sanity). Each smell carries a severity: FAIL smells gate, WARN smells
  report.

Thresholds were locked at the project's acceptance gate from the 38-repo
matrix data (each enforced value leaves ~30% headroom past the worst clean
repo of its tier). The degradation-honesty check: a dominant language with
no import support must yield a structural tour — no execution-flow
vocabulary, layers labeled canonical.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

# --- Enforced thresholds (locked from the 38-repo matrix) -------------------
#
# Density floors (FAIL): dominant-language imports/file below the tier floor.
#   full    0.9  — matrix minimum 1.31 (sinatra, post-stdlib-filter honest)
#   partial 0.8  — matrix minimum 1.17 (jason) among repos at/above the
#                  small-repo cutoff
# Repos with fewer than SMALL_REPO_FILES dominant-language files are exempt
# from all three tier gates (edgeless, floor, ceiling): a 3-file repo's
# density is noise, not evidence (roblox-lua-promise 0.67), and a 2-file
# repo with no imports between its files is normal. Same rationale as the
# curation graph-mode small-repo skip; tiny-repo honesty is still covered
# by the degradation_honesty and tour smells.
DENSITY_FLOORS = {"full": 0.9, "partial": 0.8}
SMALL_REPO_FILES = 10
# Orphan-ratio ceilings (FAIL): dominant language only.
#   full    0.30 — matrix maximum 0.232 (django: test-heavy, honest)
#   partial 0.35 — matrix maximum 0.25 (plug/jason: elixir fully-qualified
#                  module references need no alias — recorded residual)
ORPHAN_CEILINGS = {"full": 0.30, "partial": 0.35}
# "Graph claims support but is effectively edgeless" — far below the tier
# floors; kept for the message's specificity on totally broken graphs.
EDGELESS_FLOOR = 0.05
DENSITY_REGRESSION_TOLERANCE = 0.15  # >15% drop vs baseline fails; conscious
# baseline updates are the escape hatch — that friction is the feature.
CATCHALL_WARN_FRACTION = 0.50  # one layer holding >50% of layered nodes
# WARN escalates to FAIL when a single layer holds >95% in a repo with at
# least CATCHALL_FAIL_MIN_FILES code files: layering effectively failed.
# Tiny flat repos legitimately approach one layer (cowlib 95.1% at 29 code
# files is exempt via the size gate; dfmt 93% at 256 files passes).
CATCHALL_FAIL_FRACTION = 0.95
CATCHALL_FAIL_MIN_FILES = 30
LAYER_COUNT_MIN, LAYER_COUNT_MAX = 2, 12  # sanity for repos > LAYER_COUNT_MIN_FILES
LAYER_COUNT_MIN_FILES = 30
# --- Curated wiki modules (kg.modules, derive_modules output) ---------------
# Mirrors kg_curation's granularity window and naming invariants so a broken
# derivation fails the matrix before it ships a wiki regen.
MODULE_TARGET_MAX = 120  # split window; flat dirs may honestly exceed (WARN)
MODULE_MIN_FILES = 3  # layers below this legitimately yield no module
MODULE_COUNT_DIVISOR = 8  # module count must stay within [1, code_files/8]
MODULE_GENERIC_FRACTION = 0.60  # segment in >60% of paths = namespace noise
MODULES_EXPECTED_MIN_FILES = 30  # curated repos this big should have modules
TEST_MODULE_MIN_FILES = 30  # Test layers this big should surface test modules

# Execution-flow vocabulary a structural tour must never use ("named like an
# entry file" is the one sanctioned entry phrasing; "import analysis isn't
# supported" / "import resolution is incomplete" are the honesty disclaimers
# and deliberately absent from this list).
FLOW_CLAIM_TERMS = (
    "entry point",
    "imports fan out",
    "imports deep",
    "import path",
    "directly used by the entry",
    "widely-imported",
)

_EXAMPLE_DIRS = {"examples", "_examples", "example", "samples", "sample", "demo", "demos"}
_TEST_DIRS = {"tests", "test", "__tests__", "e2e"}


@dataclass
class Smell:
    severity: str  # "FAIL" | "WARN"
    code: str
    message: str


@dataclass
class RepoReport:
    repo: str
    stats: dict
    smells: list[Smell] = field(default_factory=list)
    # Full module inventory for the human-review markdown report
    # (--modules-report); deliberately NOT serialized into baselines —
    # stats["modules"] carries the diffable snapshot.
    modules_detail: list[dict] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(s.severity == "FAIL" for s in self.smells)

    def as_dict(self) -> dict:
        return {
            "repo": self.repo,
            "stats": self.stats,
            "smells": [
                {"severity": s.severity, "code": s.code, "message": s.message}
                for s in self.smells
            ],
        }


def looks_like_test(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    parts = [p.lower() for p in PurePosixPath(path).parts]
    return (
        name.startswith("test_")
        or name == "conftest.py"
        or ".test." in name
        or ".spec." in name
        or any(p in _TEST_DIRS for p in parts[:-1])
    )


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def compute_stats(kg: dict, import_support: dict[str, str] | None = None) -> dict:
    """Per-language graph-quality stats from the exported KG dict.

    ``import_support`` maps language tag -> "full" | "partial" | "none"
    (from the language registry); unknown languages count as "none".
    """
    support = import_support or {}
    file_nodes = [n for n in kg.get("nodes", []) if n.get("type") == "file" and n.get("filePath")]
    lang_by_id = {f"file:{n['filePath']}": (n.get("language") or "unknown") for n in file_nodes}

    by_lang: dict[str, dict] = {}
    for n in file_nodes:
        lang = n.get("language") or "unknown"
        bucket = by_lang.setdefault(
            lang,
            {
                "files": 0,
                "import_edges": 0,
                "internal_targets": 0,
                "external_targets": 0,
                "hint_edges": {},
                "files_with_edges": set(),
            },
        )
        bucket["files"] += 1

    for e in kg.get("edges", []):
        if e.get("type") not in ("imports", "tested_by"):
            continue
        src, dst = e.get("source", ""), e.get("target", "")
        lang = lang_by_id.get(src)
        if lang is None:
            continue
        bucket = by_lang.get(lang)
        if bucket is None:
            continue
        bucket["import_edges"] += 1
        # Convention-pass edges (e.g. hint="same_package") counted separately
        # so declared-import density and synthesised density are diffable.
        hint = e.get("hint")
        if hint:
            bucket["hint_edges"][hint] = bucket["hint_edges"].get(hint, 0) + 1
        if dst.startswith("file:external:"):
            bucket["external_targets"] += 1
        else:
            bucket["internal_targets"] += 1
            if dst in lang_by_id:
                by_lang[lang_by_id[dst]]["files_with_edges"].add(dst)
        bucket["files_with_edges"].add(src)

    out: dict[str, dict] = {}
    for lang, b in sorted(by_lang.items()):
        files = b["files"]
        resolved = b["internal_targets"]
        total_t = b["internal_targets"] + b["external_targets"]
        out[lang] = {
            "files": files,
            "import_edges": b["import_edges"],
            "edges_per_file": round(b["import_edges"] / files, 2) if files else 0.0,
            "resolution_rate": round(resolved / total_t, 3) if total_t else None,
            "orphan_files": files - len(b["files_with_edges"]),
            "orphan_ratio": round((files - len(b["files_with_edges"])) / files, 3)
            if files
            else 0.0,
            "import_support": support.get(lang, "none"),
        }
        if b["hint_edges"]:
            out[lang]["hint_edges"] = dict(sorted(b["hint_edges"].items()))

    dominant = max(out, key=lambda k: out[k]["files"], default=None)
    stats = {"by_language": out, "dominant_language": dominant}

    # Curated wiki module snapshot — lands in baselines so naming or count
    # drift across runs shows up as a reviewable diff, like tour_paths.
    modules = kg.get("modules") or []
    if modules:
        sizes = [len(m.get("nodeIds", [])) for m in modules]
        stats["modules"] = {
            "count": len(modules),
            "names": sorted(m.get("name", "") for m in modules),
            "min_size": min(sizes, default=0),
            "max_size": max(sizes, default=0),
        }
    return stats


# ---------------------------------------------------------------------------
# Smells
# ---------------------------------------------------------------------------


def run_smells(
    kg: dict,
    stats: dict,
    baseline: dict | None = None,
) -> list[Smell]:
    smells: list[Smell] = []
    layers = kg.get("layers") or []
    tour = kg.get("tour") or []
    project = kg.get("project") or {}

    ordered = [lyr["name"] for lyr in sorted(layers, key=lambda x: x.get("display_order", 0))]

    # -- original 7 (from /tmp inspect.py) ---------------------------------
    if "Test" in ordered and ordered[-1] != "Test":
        smells.append(Smell("FAIL", "test_layer_not_last", f"layer order: {ordered}"))

    for s in tour[:4]:
        tp = s.get("target_path") or ""
        if "test suite" in (s.get("reason") or "").lower():
            continue  # the designated closing stop — short tours put it early
        if s.get("kind") != "overview" and looks_like_test(tp):
            smells.append(
                Smell("FAIL", "test_file_early_in_tour", f"step {s.get('order')} = {tp}")
            )

    for s in tour:
        if "Top of the stack" in (s.get("reason") or ""):
            smells.append(
                Smell("FAIL", "stack_position_reason", f"step {s.get('order')}")
            )

    targets = [s.get("target_path") for s in tour]
    dupes = sorted({t for t in targets if t and targets.count(t) > 1})
    if dupes:
        smells.append(Smell("FAIL", "duplicate_tour_targets", str(dupes)))

    for s in tour:
        tp = s.get("target_path") or ""
        parts = PurePosixPath(tp).parts
        if any(seg.lower() in _EXAMPLE_DIRS for seg in parts[:-1]):
            smells.append(Smell("FAIL", "example_file_in_tour", f"step {s.get('order')} = {tp}"))
        if s.get("kind") == "code" and parts and parts[0].startswith("."):
            smells.append(Smell("FAIL", "dot_dir_file_in_tour", f"step {s.get('order')} = {tp}"))

    if len(tour) < 6:
        smells.append(Smell("WARN", "tour_too_short", f"only {len(tour)} steps"))

    # -- graph-level -------------------------------------------------------
    by_lang = stats.get("by_language", {})
    dominant = stats.get("dominant_language")
    dom = by_lang.get(dominant, {}) if dominant else {}

    # All three dominant-tier gates exempt repos under SMALL_REPO_FILES
    # dominant-language files: a 2-file repo with no imports between its
    # files is normal, not broken — tiny-repo honesty is covered by the
    # degradation_honesty and tour smells instead.
    if (
        dom
        and dom.get("import_support") in ("full", "partial")
        and dom.get("files", 0) >= SMALL_REPO_FILES
    ):
        if dom.get("edges_per_file", 0.0) < EDGELESS_FLOOR:
            smells.append(
                Smell(
                    "FAIL",
                    "edgeless_graph",
                    f"{dominant} declares import_support={dom['import_support']} but "
                    f"edges_per_file={dom.get('edges_per_file')}",
                )
            )
        floor = DENSITY_FLOORS[dom["import_support"]]
        if dom.get("edges_per_file", 0.0) < floor:
            smells.append(
                Smell(
                    "FAIL",
                    "density_floor",
                    f"{dominant} ({dom['import_support']}) edges_per_file="
                    f"{dom.get('edges_per_file')} < tier floor {floor}",
                )
            )
        ceiling = ORPHAN_CEILINGS[dom["import_support"]]
        if dom.get("orphan_ratio", 0.0) > ceiling:
            smells.append(
                Smell(
                    "FAIL",
                    "orphan_ceiling",
                    f"{dominant} ({dom['import_support']}) orphan_ratio="
                    f"{dom.get('orphan_ratio')} > tier ceiling {ceiling}",
                )
            )

    # -- degradation honesty -----------------------------------------------
    if dom and dom.get("import_support") == "none":
        mode = project.get("graph_mode")
        if mode != "structural":
            smells.append(
                Smell(
                    "FAIL",
                    "degradation_honesty",
                    f"{dominant} has import_support=none but graph_mode={mode!r}",
                )
            )
        for s in tour:
            reason = (s.get("reason") or "").lower()
            hit = next((t for t in FLOW_CLAIM_TERMS if t in reason), None)
            if hit:
                smells.append(
                    Smell(
                        "FAIL",
                        "degradation_honesty",
                        f"step {s.get('order')} claims flow ({hit!r}): {s.get('reason')!r}",
                    )
                )
        for layer in layers:
            if layer.get("order_basis") != "canonical":
                smells.append(
                    Smell(
                        "FAIL",
                        "degradation_honesty",
                        f"layer {layer.get('name')} order_basis="
                        f"{layer.get('order_basis')!r} on an edgeless graph",
                    )
                )

    if baseline:
        base_langs = (baseline.get("stats") or {}).get("by_language", {})
        for lang, cur in by_lang.items():
            prev = base_langs.get(lang)
            if not prev or prev.get("edges_per_file", 0) <= 0:
                continue
            drop = 1.0 - (cur.get("edges_per_file", 0.0) / prev["edges_per_file"])
            if drop > DENSITY_REGRESSION_TOLERANCE:
                smells.append(
                    Smell(
                        "FAIL",
                        "density_regression",
                        f"{lang}: {prev['edges_per_file']} -> {cur.get('edges_per_file')} "
                        f"imports/file ({drop:.0%} drop)",
                    )
                )

    code_file_count = sum(
        b["files"] for lang, b in by_lang.items() if b.get("import_support") != "none"
    ) or sum(b["files"] for b in by_lang.values())
    if layers and code_file_count:
        biggest = max(layers, key=lambda lyr: len(lyr.get("nodeIds", [])))
        frac = len(biggest.get("nodeIds", [])) / max(
            1, sum(len(lyr.get("nodeIds", [])) for lyr in layers)
        )
        if frac > CATCHALL_FAIL_FRACTION and code_file_count >= CATCHALL_FAIL_MIN_FILES:
            smells.append(
                Smell(
                    "FAIL",
                    "catchall_layer",
                    f"{biggest.get('name')} holds {frac:.0%} of layered nodes "
                    f"({code_file_count} code files — layering effectively failed)",
                )
            )
        elif frac > CATCHALL_WARN_FRACTION:
            smells.append(
                Smell(
                    "WARN",
                    "catchall_layer",
                    f"{biggest.get('name')} holds {frac:.0%} of layered nodes",
                )
            )

    total_files = project.get("total_files", 0)
    if (
        total_files > LAYER_COUNT_MIN_FILES
        and layers
        and not (LAYER_COUNT_MIN <= len(layers) <= LAYER_COUNT_MAX)
    ):
        smells.append(
            Smell("WARN", "layer_count", f"{len(layers)} layers for {total_files} files")
        )

    # -- entry-point sanity --------------------------------------------------
    eps = project.get("entry_points") or []
    node_paths = {n.get("filePath") for n in kg.get("nodes", []) if n.get("filePath")}
    for ep in eps:
        if ep not in node_paths:
            smells.append(Smell("FAIL", "entry_point_missing", f"{ep} not in graph"))
        if looks_like_test(ep):
            smells.append(Smell("FAIL", "entry_point_is_test", ep))
        parts = PurePosixPath(ep).parts
        if any(seg.lower() in _EXAMPLE_DIRS for seg in parts[:-1]):
            smells.append(Smell("FAIL", "entry_point_is_example", ep))
    if not eps:
        for s in tour:
            if "An entry point" in (s.get("reason") or ""):
                smells.append(
                    Smell(
                        "FAIL",
                        "entry_claim_without_entry_points",
                        f"step {s.get('order')} claims an entry point but entry_points=[]",
                    )
                )

    smells.extend(_module_smells(kg, code_file_count))

    return smells


def _dominant_path_segments(paths: list[str]) -> set[str]:
    """Dir segments in > MODULE_GENERIC_FRACTION of *paths* (namespace noise).

    Mirrors kg_curation's data-driven stripping: ``src``, ``packages``, the
    repo's own package name. A module *named* one of these tells the reader
    nothing.
    """
    n = len(paths)
    if not n:
        return set()
    counts: dict[str, int] = {}
    for p in paths:
        for seg in set(PurePosixPath(p).parts[:-1]):
            counts[seg] = counts.get(seg, 0) + 1
    return {s.lower() for s, c in counts.items() if c / n > MODULE_GENERIC_FRACTION}


def _module_smells(kg: dict, code_file_count: int) -> list[Smell]:
    """Curated wiki module invariants over the exported ``kg.modules``.

    The derivation's unit tests cover synthetic shapes; these checks catch
    what only real repos surface — Java namespace prefixes leaking into
    names, confetti module counts on flat-package repos, Test layers losing
    their modules on test-heavy codebases.
    """
    smells: list[Smell] = []
    modules = kg.get("modules") or []
    layers = kg.get("layers") or []
    file_paths = {
        n["id"]: n["filePath"]
        for n in kg.get("nodes", [])
        if n.get("type") == "file" and n.get("filePath")
    }

    if not modules:
        # Curated layers without modules = derivation degraded. Tiny repos
        # legitimately yield none (every layer under MODULE_MIN_FILES).
        if layers and code_file_count >= MODULES_EXPECTED_MIN_FILES:
            smells.append(
                Smell(
                    "WARN",
                    "modules_missing",
                    f"curated layers present but no modules for {code_file_count} code files",
                )
            )
        return smells

    # Partition: no file in two modules (coverage is checked per-layer below).
    seen: set[str] = set()
    for m in modules:
        for nid in m.get("nodeIds", []):
            if nid in seen:
                smells.append(
                    Smell("FAIL", "module_partition", f"{file_paths.get(nid, nid)} in >1 module")
                )
                return smells  # one witness is enough; rest would be noise
            seen.add(nid)

    # Coverage: every layer big enough to module must be fully covered.
    for layer in layers:
        member_files = [nid for nid in layer.get("nodeIds", []) if nid in file_paths]
        if len(member_files) < MODULE_MIN_FILES:
            continue
        missing = [nid for nid in member_files if nid not in seen]
        if missing:
            smells.append(
                Smell(
                    "FAIL",
                    "module_coverage",
                    f"layer {layer.get('name')}: {len(missing)} files in no module "
                    f"(e.g. {file_paths.get(missing[0], missing[0])})",
                )
            )

    # Naming invariants: unique, no size suffixes, no generic-only names.
    names = [m.get("name", "") for m in modules]
    ids = [m.get("id", "") for m in modules]
    for label, values in (("module_name_collision", names), ("module_id_collision", ids)):
        dupes = sorted({v for v in values if values.count(v) > 1})
        if dupes:
            smells.append(Smell("FAIL", label, str(dupes)))
    generic = _dominant_path_segments(sorted(file_paths.values()))
    layer_names = {(layer.get("name") or "").lower() for layer in layers}
    for m in modules:
        name = m.get("name", "")
        if not name.strip() or name.strip().lower() in {"(root)", "root"}:
            smells.append(Smell("FAIL", "module_empty_name", repr(name)))
            continue
        if re.search(r"\(\d+\)\s*$", name):
            smells.append(Smell("FAIL", "module_size_suffix", name))
        segs = [s.lower() for s in name.split("/")]
        # Two honest exemptions: layer-named modules (whole-layer fallback —
        # "API" names the API layer even if "api" is also a path segment),
        # and modules whose OWN path offers no informative segment (fixture
        # subtrees that dominate the repo: the raw tail is the best name
        # available, flagging it would demand a name that can't exist).
        path_segs = {s.lower() for s in (m.get("path") or "").split("/") if s}
        had_alternative = bool(path_segs - generic)
        if (
            segs
            and all(s in generic for s in segs)
            and name.lower() not in layer_names
            and had_alternative
        ):
            smells.append(
                Smell("FAIL", "module_generic_name", f"{name!r} is namespace noise only")
            )

    # Granularity: oversized modules are honest only for flat directories.
    for m in modules:
        size = len(m.get("nodeIds", []))
        if size <= MODULE_TARGET_MAX:
            continue
        prefix = m.get("path", "")
        rels = [
            file_paths[nid][len(prefix) + 1 :] if prefix and file_paths[nid].startswith(prefix + "/")
            else file_paths[nid]
            for nid in m.get("nodeIds", [])
            if nid in file_paths
        ]
        if any("/" in r for r in rels):
            smells.append(
                Smell(
                    "WARN",
                    "module_oversized",
                    f"{m.get('name')} has {size} files with subdirs (window {MODULE_TARGET_MAX})",
                )
            )

    # Count bounds: confetti detection ([1, code_files/8] acceptance gate).
    # Small repos are exempt — a 12-file repo with two honest 6-file modules
    # is structure, not confetti; the gate targets matrix-scale repos.
    ceiling = max(1, code_file_count // MODULE_COUNT_DIVISOR)
    if code_file_count >= MODULES_EXPECTED_MIN_FILES and len(modules) > ceiling:
        smells.append(
            Smell(
                "WARN",
                "module_count",
                f"{len(modules)} modules for {code_file_count} code files (ceiling {ceiling})",
            )
        )

    # Test-heavy repos must keep their test modules (user-valued invariant).
    for layer in layers:
        if (layer.get("name") or "").lower() != "test":
            continue
        test_files = [nid for nid in layer.get("nodeIds", []) if nid in file_paths]
        if len(test_files) >= TEST_MODULE_MIN_FILES:
            layer_id = layer.get("id", "")
            if not any(m.get("layerId") == layer_id for m in modules):
                smells.append(
                    Smell(
                        "WARN",
                        "test_modules_missing",
                        f"Test layer has {len(test_files)} files but no module",
                    )
                )

    return smells
