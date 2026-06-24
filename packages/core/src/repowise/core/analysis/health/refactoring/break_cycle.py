"""Break Cycle detector — name the import edge to cut.

A strongly-connected component of the file dependency graph is a circular
import: a set of files that mutually (transitively) depend on each other, so
none can be understood, tested, or moved in isolation. The fix is to break
the cycle by inverting or abstracting a minimal set of edges. This detector
names that minimal set — it does *not* invent the abstraction (that is left
to a human or an opt-in code-gen step); it answers the hard part
deterministically: *which* edge to cut.

The cycle is not a heuristic: it is a strongly-connected component the graph
already encodes (computed once by the engine and threaded in as
``ctx.file_scc``). The minimal cut is the feedback arc set — NP-hard exactly,
so we use the classic greedy feedback-arc-set heuristic: order the nodes to
minimise backward edges, then the backward edges are the arcs to remove. It
is deterministic (sorted tie-breaks) and near-optimal on the small, dense
components real import cycles form.

A cycle spans files, so — like Extract Helper — the suggestion is emitted
only from the **canonical anchor** (the lexicographically smallest member),
so one cycle yields one suggestion, not one per file.
"""

from __future__ import annotations

from .graph_signals import cycle_edges
from .models import RefactoringContext, RefactoringSuggestion
from .registry import RefactoringDetector, register

# Break Cycle answers "which edge to cut" — that only stays actionable when
# the cut is small and the cycle surveyable. A sprawling barrel-import tangle
# (a package whose __init__ re-exports dozens of submodules that import the
# package back) yields a huge SCC with a hundred-edge cut: real, but not a
# "name the edge" suggestion. Those are dropped here (a coarser package-layout
# finding, out of scope) so the surface only carries surgical, trustworthy cuts.
_MAX_CYCLE_FILES = 20
_MAX_CUT_EDGES = 4


def _greedy_mfas(members: tuple[str, ...], edges: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Approximate the minimum feedback arc set with the greedy ordering heuristic.

    Returns the backward edges of a greedy linear ordering — a small set whose
    removal makes the component acyclic. Deterministic: every choice breaks
    ties on sorted node order, so the same cycle always yields the same cut.
    """
    nodes = list(members)
    adj_out: dict[str, set[str]] = {n: set() for n in nodes}
    adj_in: dict[str, set[str]] = {n: set() for n in nodes}
    node_set = set(nodes)
    for u, v in edges:
        if u in node_set and v in node_set and u != v:
            adj_out[u].add(v)
            adj_in[v].add(u)

    remaining = set(nodes)
    s1: list[str] = []  # sources side (front of the ordering)
    s2: list[str] = []  # sinks side (back of the ordering)

    def _out_deg(n: str) -> int:
        return len(adj_out[n] & remaining)

    def _in_deg(n: str) -> int:
        return len(adj_in[n] & remaining)

    while remaining:
        progressed = True
        while progressed:
            progressed = False
            for n in sorted(remaining):
                if _out_deg(n) == 0:  # sink → prepend to s2
                    s2.insert(0, n)
                    remaining.discard(n)
                    progressed = True
            for n in sorted(remaining):
                if n in remaining and _in_deg(n) == 0:  # source → append to s1
                    s1.append(n)
                    remaining.discard(n)
                    progressed = True
        if remaining:
            # Pick the node most "source-like" (out-degree minus in-degree);
            # lexicographic tie-break keeps the ordering deterministic.
            best = max(sorted(remaining), key=lambda n: _out_deg(n) - _in_deg(n))
            s1.append(best)
            remaining.discard(best)

    ordering = s1 + s2
    pos = {n: i for i, n in enumerate(ordering)}
    # Backward edges (target earlier than source in the ordering) are the cut.
    # Both endpoints are always in ``pos`` (the ordering covers every member and
    # ``edges`` is restricted to member-member edges) — the explicit membership
    # check keeps a malformed edge from silently yielding an incomplete cut.
    cut = [(u, v) for (u, v) in edges if u in pos and v in pos and pos[u] > pos[v]]
    return sorted(set(cut))


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


@register
class BreakCycleDetector(RefactoringDetector):
    name = "break_cycle"

    def detect(self, ctx: RefactoringContext) -> list[RefactoringSuggestion]:
        members = ctx.file_scc
        if not members or len(members) < 2:
            return []
        # Surgical cuts only: a giant barrel-import tangle is not a "name the
        # edge" refactoring (see module note).
        if len(members) > _MAX_CYCLE_FILES:
            return []
        # Canonical anchor: emit each cycle exactly once, from its smallest
        # member path (members is sorted by the engine's SCC index).
        if ctx.file_path != members[0]:
            return []
        if ctx.graph is None:
            return []

        edges = cycle_edges(ctx.graph, members)
        if not edges:
            return []
        cut = _greedy_mfas(members, edges)
        if not cut or len(cut) > _MAX_CUT_EDGES:
            return []

        size = len(members)
        plan = {
            "cycle": list(members),
            "cut_edges": [{"from": u, "to": v} for u, v in cut],
        }
        evidence = {
            "cycle_size": size,
            "edge_count": len(edges),
            "cut_count": len(cut),
        }
        blast_radius = {"files": list(members), "file_count": size}
        # A short cut on a small cycle is the cleanest, highest-confidence
        # break; a sprawling component with many back-edges is murkier.
        confidence = "high" if len(cut) == 1 and size <= 4 else "medium"
        cut_label = ", ".join(f"{_basename(u)}->{_basename(v)}" for u, v in cut)
        return [
            RefactoringSuggestion(
                refactoring_type=self.name,
                file_path=ctx.file_path,
                target_symbol=f"cycle[{size}]: {cut_label}",
                line_start=None,
                line_end=None,
                plan=plan,
                evidence=evidence,
                impact_delta=0.0,
                effort_bucket=_cycle_effort(size),
                blast_radius=blast_radius,
                confidence=confidence,
                source_biomarker="",
            )
        ]


def _cycle_effort(size: int) -> str:
    """Effort scales with how many files the break touches."""
    if size <= 2:
        return "S"
    if size <= 4:
        return "M"
    if size <= 8:
        return "L"
    return "XL"
