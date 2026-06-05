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

Phase 0 contract: thresholds are deliberately permissive (observe first,
enforce in Phase 5); the degradation-honesty vocabulary check ships disabled
until the structural tour mode exists (Phase 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

# Density floors per import-support tier. Phase 0: only "broken graph"
# obviousness; Phase 5 locks real per-language floors from matrix data.
EDGELESS_FLOOR = 0.05  # imports/file below this with full/partial support = broken
DENSITY_REGRESSION_TOLERANCE = 0.15  # >15% drop vs baseline fails
CATCHALL_WARN_FRACTION = 0.50  # one layer holding >50% of code files
LAYER_COUNT_MIN, LAYER_COUNT_MAX = 2, 12  # sanity for repos > LAYER_COUNT_MIN_FILES
LAYER_COUNT_MIN_FILES = 30

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

    dominant = max(out, key=lambda k: out[k]["files"], default=None)
    return {"by_language": out, "dominant_language": dominant}


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

    ordered = [l["name"] for l in sorted(layers, key=lambda x: x.get("display_order", 0))]

    # -- original 7 (from /tmp inspect.py) ---------------------------------
    if "Test" in ordered and ordered[-1] != "Test":
        smells.append(Smell("FAIL", "test_layer_not_last", f"layer order: {ordered}"))

    for s in tour[:4]:
        tp = s.get("target_path") or ""
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

    if dom and dom.get("import_support") in ("full", "partial"):
        if dom.get("edges_per_file", 0.0) < EDGELESS_FLOOR:
            smells.append(
                Smell(
                    "FAIL",
                    "edgeless_graph",
                    f"{dominant} declares import_support={dom['import_support']} but "
                    f"edges_per_file={dom.get('edges_per_file')}",
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
        biggest = max(layers, key=lambda l: len(l.get("nodeIds", [])))
        frac = len(biggest.get("nodeIds", [])) / max(
            1, sum(len(l.get("nodeIds", [])) for l in layers)
        )
        if frac > CATCHALL_WARN_FRACTION:
            smells.append(
                Smell(
                    "WARN",
                    "catchall_layer",
                    f"{biggest.get('name')} holds {frac:.0%} of layered nodes",
                )
            )

    total_files = project.get("total_files", 0)
    if total_files > LAYER_COUNT_MIN_FILES and layers:
        if not (LAYER_COUNT_MIN <= len(layers) <= LAYER_COUNT_MAX):
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

    return smells
