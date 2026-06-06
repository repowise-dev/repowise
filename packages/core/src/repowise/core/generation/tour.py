"""Topology-driven guided tour.

The guided tour is an **ordering + curation** layer over wiki pages that
already exist within the user's coverage budget. It is *not* a generator of
new expensive pages: every stop points at a page the selection layer already
chose (a ``file_page`` / ``module_page`` / ``infra_page`` / ``repo_overview``)
plus a short, deterministic "why you're here" reason. A single onboarding
page later narrates the whole ordered sequence in one LLM call, so the tour
adds at most one page regardless of repo size.

How the order is derived (all deterministic — reuses signals the generation
run already computes):

1. **Entry-point scoring** (:func:`score_entry_points`) — filename heuristics
   (``main``/``app``/``server``/…), our ``is_entry_point`` ingestion flag,
   shallow path depth, and PageRank. The top scorers seed the walk.
2. **Breadth-first walk** over the import graph from those seeds, assigning
   each reachable file a *depth* (how many imports away from an entry point).
3. **Depth-bucketed step order** — the reader moves from entry points inward,
   so step N's narration can back-reference what step N-1 introduced.
4. **Non-code woven in** — infrastructure pages land at the end ("how it's
   built and deployed"); the repo overview always opens the tour.

Because the tour only references already-selected pages, it honours the
coverage budget by construction. :func:`tour_landmark_paths` lets the selector
*guarantee* the handful of highest-value entry points get a page (see
``selection.selector`` for how that is kept count-honest).
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from repowise.core.generation.layers import ADJACENT_LAYERS, infer_layer, is_support_path
from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY

# Filenames that conventionally mark an executable / wiring entry point.
# Derived from the language registry (generic stems + per-language stems);
# parity with the historical hard-coded set is pinned by
# tests/unit/ingestion/test_language_capabilities.py.
_ENTRY_FILENAME_STEMS: frozenset[str] = _LANG_REGISTRY.entry_filename_stems()

# A genuine entry whose forward BFS reaches no more than this many files is a
# wiring stub (supervision/bootstrap); the widest-fanout file then co-anchors.
_STUB_ENTRY_REACH = 3

# Languages whose files can never be execution entry points, however
# entry-like their stem is: docs/data/config (``is_code=False`` —
# docs/index.md is not a program's front door, and neither is
# schema.graphql or an openapi index) plus build/deploy wiring
# (``is_infra`` — a Dockerfile or deploy.sh wires the system rather than
# starting its control flow; infra pages close the tour instead). Both
# sets derive from the registry; the lowercase aliases guard against
# unnormalized language strings from older indexes (none is a spec tag).
_NON_CODE_ALIASES: frozenset[str] = frozenset(
    {"md", "yml", "txt", "html", "css", "csv", "xml", "svg", "rst", "text", "ini", "cmake"}
)
_NON_CODE_LANGUAGES: frozenset[str] = (
    _LANG_REGISTRY.config_languages()
    | _LANG_REGISTRY.infra_languages()
    | _NON_CODE_ALIASES
)

# Upper bound on tour stops — long enough to teach the spine, short enough to
# stay a tour and not a table of contents.
DEFAULT_MAX_STOPS = 12

# How many top-scored entry points the selector is asked to guarantee a page
# for, so the tour can always open on real landmark documentation.
DEFAULT_MAX_LANDMARKS = 5


@dataclass(frozen=True)
class TourStop:
    """One ordered stop. Always points at a page that already exists."""

    order: int
    target_path: str
    page_type: str
    title: str
    depth: int
    kind: str  # "overview" | "code" | "infra"
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "target_path": self.target_path,
            "page_type": self.page_type,
            "title": self.title,
            "depth": self.depth,
            "kind": self.kind,
            "reason": self.reason,
        }


def _path_depth(path: str) -> int:
    """Directory depth — 0 for a root file, 1 for one level deep, etc."""
    return max(0, len(PurePosixPath(path).parts) - 1)


def score_entry_points(
    parsed_files: Sequence[Any],
    pagerank: Mapping[str, float],
) -> list[tuple[float, str]]:
    """Score code files as candidate tour entry points, descending.

    Scoring (additive):
      * ``is_entry_point`` ingestion flag .............. +3 (code files only)
      * filename stem in the entry-name set ............ +3 (code files only)
      * file at repo root or one level deep ............ +1
      * PageRank in the top 10% of all files ........... +1

    Both entry bonuses are withheld from doc/data languages — ``docs/index.md``
    must never outrank a real ``main.py``, even when ingestion's stem rule
    flagged it — from test files (``tests/testserver/server.py`` is a fixture,
    not where a reader enters the system), and from example/demo dirs (every
    ``examples/*/main.go`` is an entry by *name*; none is the system).

    Returns ``[(score, path), ...]`` sorted by score then path for stability.
    Only files with a positive score are returned.
    """
    if not parsed_files:
        return []
    pr_values = sorted((pagerank.get(p.file_info.path, 0.0) for p in parsed_files), reverse=True)
    top_decile_idx = max(0, int(len(pr_values) * 0.10) - 1)
    pr_threshold = pr_values[top_decile_idx] if pr_values else 0.0

    scored: list[tuple[float, str]] = []
    for p in parsed_files:
        fi = p.file_info
        path = fi.path
        score = 0.0
        language = (getattr(fi, "language", "") or "").lower()
        entry_eligible = (
            language not in _NON_CODE_LANGUAGES
            and infer_layer(path, language) not in ADJACENT_LAYERS
            and not is_support_path(path)
        )
        if entry_eligible and getattr(fi, "is_entry_point", False):
            score += 3.0
        if entry_eligible and PurePosixPath(path).stem.lower() in _ENTRY_FILENAME_STEMS:
            score += 3.0
        if _path_depth(path) <= 1:
            score += 1.0
        if pagerank.get(path, 0.0) >= pr_threshold > 0.0:
            score += 1.0
        if score > 0.0:
            scored.append((score, path))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return scored


def tour_landmark_paths(
    parsed_files: Sequence[Any],
    pagerank: Mapping[str, float],
    *,
    max_landmarks: int = DEFAULT_MAX_LANDMARKS,
) -> list[str]:
    """Return the top entry-point file paths the tour wants guaranteed a page.

    The selector force-includes these into the ``file_page`` allow-set
    (displacing its lowest-scored picks so the page count stays honest), so
    the tour can always open on real landmark documentation even at low
    coverage.
    """
    scored = score_entry_points(parsed_files, pagerank)
    return [path for _, path in scored[: max(0, max_landmarks)]]


def _bfs_depths(
    seeds: list[str],
    adjacency: Mapping[str, list[str]],
    universe: set[str],
) -> dict[str, int]:
    """Assign a BFS depth to each file reachable from *seeds* within *universe*."""
    depth: dict[str, int] = {}
    queue: deque[tuple[str, int]] = deque()
    for s in seeds:
        if s in universe and s not in depth:
            depth[s] = 0
            queue.append((s, 0))
    while queue:
        node, d = queue.popleft()
        for dep in adjacency.get(node, []):
            if dep in universe and dep not in depth:
                depth[dep] = d + 1
                queue.append((dep, d + 1))
    return depth


def build_tour(
    parsed_files: Sequence[Any],
    pagerank: Mapping[str, float],
    import_edges: Iterable[tuple[str, str]],
    *,
    file_page_paths: Iterable[str],
    infra_paths: Iterable[str] = (),
    repo_name: str = "",
    max_stops: int = DEFAULT_MAX_STOPS,
    graph_mode: str = "flow",
    anchor_rank: Mapping[str, int] | None = None,
) -> list[TourStop]:
    """Build the ordered tour over pages that already exist.

    Only files in *file_page_paths* (and *infra_paths*) become stops — the
    tour never references undocumented files, so it is budget-safe by
    construction. ``repo_overview`` (when *repo_name* is given) always opens
    the tour; infrastructure pages close it.

    ``graph_mode="sparse"`` keeps the BFS walk but softens the reasons that
    interpret *absence* of edges: on a sparse graph (broken/partial import
    resolution), "off the import path" would blame the file for the
    resolver's gaps. Reasons grounded in edges that DO exist are unchanged.
    Truly edgeless repos should not walk at all — the caller is expected to
    build a structural tour instead (``graph_mode="structural"`` is handled
    by the curation layer, not here).
    """
    documented = set(file_page_paths)
    infra = set(infra_paths)
    adjacency: dict[str, list[str]] = defaultdict(list)
    fan_in: dict[str, int] = defaultdict(int)
    for src, dst in import_edges:
        if src.startswith("external:") or dst.startswith("external:"):
            continue
        adjacency[src].append(dst)
        fan_in[dst] += 1

    # Seeds = the genuine entry points (the ``is_entry_point`` flag or an
    # entry-style filename, both worth +3), restricted to documented files. A
    # mere +1 for being shallow or high-PageRank does NOT make a file a seed —
    # otherwise every root file would seed the walk and flatten all depths.
    scored = score_entry_points(parsed_files, pagerank)
    seeds = [path for s, path in scored if s >= 3.0 and path in documented]
    # Whether the seeds are genuine entry points (flag/filename evidence) or
    # just the best-available anchor — the step reasons must not overclaim.
    genuine_entries = bool(seeds)

    # Docs, config, and test files can't anchor a walk.
    ineligible = {
        p.file_info.path
        for p in parsed_files
        if getattr(p, "file_info", None)
        and (
            (getattr(p.file_info, "language", "") or "").lower() in _NON_CODE_LANGUAGES
            or infer_layer(p.file_info.path, p.file_info.language) in ADJACENT_LAYERS
            or is_support_path(p.file_info.path)
        )
    }

    # Rank by import *relationships* when the caller supplies the
    # fan-out-collapsed counts (one package import = one relationship),
    # else raw out-degree.
    def _fanout(p: str) -> int:
        if anchor_rank is not None:
            return anchor_rank.get(p, 0)
        return len(adjacency.get(p, []))

    def _best_anchor(exclude: set[str]) -> str | None:
        eligible = sorted(
            (p for p in documented if p not in ineligible and p not in exclude),
            key=lambda p: (-_fanout(p), -pagerank.get(p, 0.0), p),
        )
        return eligible[0] if eligible else None

    anchor_seeds: set[str] = set()
    if not seeds:
        # No genuine entry point anywhere (flat libraries like requests).
        # Anchor the walk on the eligible code file whose imports fan out the
        # widest — the closest thing to "execution starts here" the import
        # graph offers.
        best = _best_anchor(set())
        seeds = [best] if best else [path for _, path in scored if path in documented][:1]
    depths = _bfs_depths(seeds, adjacency, documented)

    # Wiring-stub entries: an OTP application.ex or DI bootstrap starts
    # supervisors, not the domain logic — its forward BFS dies at depth 1
    # and every walk slot fills with unreached parking. When the genuine
    # entries reach almost nothing and a non-seed file's imports fan out
    # substantially, that file co-anchors the walk (its own steps use
    # anchor wording, never "entry point").
    if genuine_entries and len(depths) <= _STUB_ENTRY_REACH and len(documented) > _STUB_ENTRY_REACH * 2:
        co_anchor = _best_anchor(set(seeds))
        if co_anchor is not None and _fanout(co_anchor) >= 3:
            anchor_seeds.add(co_anchor)
            seeds = [*seeds, co_anchor]
            depths = _bfs_depths(seeds, adjacency, documented)

    # Documented files never reached from a seed still belong in the tour;
    # park them after the deepest reached file, ranked by PageRank. Remember
    # which files were genuinely reached so their reasons stay truthful.
    # Documents and config/data files are exempt from parking: they can
    # never be on an import path, so an unreached CHANGELOG.md or
    # wally.toml is expected — not worth a tour slot that displaces code.
    non_code_paths = {
        p.file_info.path
        for p in parsed_files
        if getattr(p, "file_info", None)
        and (getattr(p.file_info, "language", "") or "").lower() in _NON_CODE_LANGUAGES
    }
    reached = set(depths)
    max_reached = max(depths.values(), default=-1)
    # Manifests (mix.exs, project.clj, Setup.lhs) are code-shaped but cannot
    # be on an import path by design — like documents, an unreached manifest
    # is expected and never worth a parked tour slot.
    manifest_names = _LANG_REGISTRY.manifest_filenames()
    for path in documented:
        if path not in depths:
            if path in non_code_paths or PurePosixPath(path).name in manifest_names:
                continue
            depths[path] = max_reached + 1
    documented = {p for p in documented if p in depths}

    pr_score = score_entry_points(parsed_files, pagerank)
    ep_score = {path: s for s, path in pr_score}

    def code_sort_key(path: str) -> tuple[int, float, str]:
        # Shallower (closer to an entry point) first; within a depth, higher
        # entry-point score then PageRank; path for determinism.
        return (
            depths[path],
            -(ep_score.get(path, 0.0) * 100 + pagerank.get(path, 0.0)),
            path,
        )

    code_order = sorted(documented, key=code_sort_key)

    stops: list[TourStop] = []
    order = 0

    if repo_name:
        order += 1
        stops.append(
            TourStop(
                order=order,
                target_path=repo_name,
                page_type="repo_overview",
                title="Repository Overview",
                depth=0,
                kind="overview",
                reason="Start here for the end-to-end picture before diving into the code.",
            )
        )

    # Reserve a couple of stops for infrastructure at the end when present.
    infra_budget = min(2, len(infra), max(0, max_stops - len(stops) - 1))
    code_budget = max(0, max_stops - len(stops) - infra_budget)

    for path in code_order[:code_budget]:
        order += 1
        d = depths[path]
        if d <= 0:
            if path in anchor_seeds:
                reason = (
                    "The walk's anchor — the entry point above only wires the "
                    "app together, so the tour follows the file whose imports "
                    "fan out the widest."
                )
            elif genuine_entries:
                reason = "An entry point — execution and imports fan out from here."
            else:
                # State the anchor's actual selection evidence (widest import
                # fan-out), not a vague "best-connected" that implies fan-in.
                reason = "The walk's anchor — its imports fan out the widest in a repo with no single entry point."
        elif path not in reached:
            if graph_mode == "sparse":
                # The graph, not the file, is the likely cause here.
                reason = (
                    "Not on the resolved import paths — import resolution is "
                    "incomplete for this repo, so links may be missing."
                )
            elif genuine_entries:
                reason = (
                    "Off the import path from the entry points — a standalone "
                    "or supporting file."
                )
            elif fan_in.get(path, 0) >= 3:
                reason = "A widely-imported module — much of the repo depends on it."
            else:
                # No fan-in evidence: a generated lookup table parked by
                # PageRank must not claim the repo depends on it.
                reason = (
                    "Off the import paths walked above — a standalone or "
                    "supporting file."
                )
        elif d == 1:
            # Anchor-seeded walks have no entry points — the reason must not
            # invent them. When a co-anchor rescued a wiring-stub entry, the
            # depth-1 files were reached through the anchor, not the entry.
            reason = (
                "Directly used by the entry points above; a core collaborator."
                if genuine_entries and not anchor_seeds
                else "Directly imported by the anchor above; a core collaborator."
            )
        else:
            reason = f"Reached {d} imports deep — a supporting building block."
        stops.append(
            TourStop(
                order=order,
                target_path=path,
                page_type="file_page",
                title=PurePosixPath(path).name,
                depth=d,
                kind="code",
                reason=reason,
            )
        )

    # Weave infrastructure in last: how the system is packaged and deployed.
    infra_ranked = sorted(infra, key=lambda p: (-pagerank.get(p, 0.0), p))
    deepest = max((s.depth for s in stops), default=0) + 1
    for path in infra_ranked[:infra_budget]:
        order += 1
        stops.append(
            TourStop(
                order=order,
                target_path=path,
                page_type="infra_page",
                title=PurePosixPath(path).name,
                depth=deepest,
                kind="infra",
                reason="How the system above is configured, built, or deployed.",
            )
        )

    return stops
