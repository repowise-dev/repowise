"""Tests for the Break Cycle refactoring detector + the SCC graph signal.

The detector consumes the per-file SCC slice the engine precomputes
(``RefactoringContext.file_scc``) plus the in-memory graph, runs a greedy
feedback-arc-set cut, and names the import edge(s) to invert. Fixtures build
small import graphs so the cycle and its minimal cut are explicit.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.analysis.health.refactoring import (
    RefactoringContext,
    build_file_scc_index,
    detect_refactorings,
)
from repowise.core.analysis.health.refactoring.break_cycle import (
    BreakCycleDetector,
    _greedy_mfas,
)


def _import_graph(edges: list[tuple[str, str]]) -> nx.DiGraph:
    g = nx.DiGraph()
    files = {f for e in edges for f in e}
    for f in files:
        g.add_node(f, node_type="file")
    for u, v in edges:
        g.add_edge(u, v, edge_type="imports")
    return g


def _detect(g: nx.DiGraph, file_path: str) -> list:
    idx = build_file_scc_index(g)
    ctx = RefactoringContext(
        file_path=file_path,
        language="python",
        nloc=50,
        graph=g,
        file_scc=idx.get(file_path),
    )
    return [s for s in detect_refactorings(ctx) if s.refactoring_type == "break_cycle"]


def test_two_file_cycle_cuts_one_edge():
    g = _import_graph([("a.py", "b.py"), ("b.py", "a.py")])
    out = _detect(g, "a.py")
    assert len(out) == 1
    s = out[0]
    assert s.evidence == {"cycle_size": 2, "edge_count": 2, "cut_count": 1}
    assert s.plan["cut_edges"] == [{"from": "b.py", "to": "a.py"}]
    assert s.plan["cycle"] == ["a.py", "b.py"]
    assert s.confidence == "high"
    assert s.blast_radius == {"files": ["a.py", "b.py"], "file_count": 2}


def test_emitted_only_from_canonical_anchor():
    g = _import_graph([("a.py", "b.py"), ("b.py", "a.py")])
    assert _detect(g, "b.py") == []  # b.py is not the smallest member


def test_no_cycle_yields_nothing():
    g = _import_graph([("a.py", "b.py"), ("b.py", "c.py")])
    assert build_file_scc_index(g) == {}
    assert _detect(g, "a.py") == []


def test_three_file_cycle_is_one_suggestion():
    g = _import_graph([("a.py", "b.py"), ("b.py", "c.py"), ("c.py", "a.py")])
    out = _detect(g, "a.py")
    assert len(out) == 1
    s = out[0]
    assert s.evidence["cycle_size"] == 3
    assert s.evidence["cut_count"] >= 1
    # The cut makes the cycle acyclic.
    members = ("a.py", "b.py", "c.py")
    cut = {(e["from"], e["to"]) for e in s.plan["cut_edges"]}
    remaining = nx.DiGraph()
    remaining.add_nodes_from(members)
    for u, v in [("a.py", "b.py"), ("b.py", "c.py"), ("c.py", "a.py")]:
        if (u, v) not in cut:
            remaining.add_edge(u, v)
    assert nx.is_directed_acyclic_graph(remaining)


def test_greedy_mfas_is_deterministic_and_minimal():
    members = ("a", "b", "c")
    edges = [("a", "b"), ("b", "c"), ("c", "a")]
    cut1 = _greedy_mfas(members, edges)
    cut2 = _greedy_mfas(members, edges)
    assert cut1 == cut2
    assert len(cut1) == 1  # a single back edge breaks a simple 3-cycle


def test_no_graph_yields_nothing():
    ctx = RefactoringContext(
        file_path="a.py",
        language="python",
        nloc=50,
        graph=None,
        file_scc=("a.py", "b.py"),
    )
    assert BreakCycleDetector().detect(ctx) == []


def test_huge_barrel_cycle_is_dropped():
    # A package __init__ that re-exports many submodules which import it back
    # forms a giant SCC with a large cut — not a "name the edge" refactoring.
    edges = [(f"m{i}.py", "pkg/__init__.py") for i in range(25)]
    edges += [("pkg/__init__.py", f"m{i}.py") for i in range(25)]
    g = _import_graph(edges)
    # The cycle exists...
    assert any(len(m) > 20 for m in build_file_scc_index(g).values())
    # ...but no surgical suggestion is emitted for an oversized tangle.
    anchor = sorted(f for e in edges for f in e)[0]
    assert _detect(g, anchor) == []


def test_large_cut_is_dropped():
    # A small cycle whose minimal cut still exceeds the cut cap is not surfaced.
    # A 6-node bidirectional clique needs many back-edges to break.
    files = [f"f{i}.py" for i in range(6)]
    edges = [(a, b) for a in files for b in files if a != b]
    g = _import_graph(edges)
    out = _detect(g, "f0.py")
    assert out == []


def test_co_change_edges_do_not_form_a_cycle():
    # Only a co_changes edge between the pair → not a structural cycle.
    g = nx.DiGraph()
    g.add_node("a.py", node_type="file")
    g.add_node("b.py", node_type="file")
    g.add_edge("a.py", "b.py", edge_type="imports")
    g.add_edge("b.py", "a.py", edge_type="co_changes")
    assert build_file_scc_index(g) == {}


# -- cycle_edges equivalence ------------------------------------------------------
#
# cycle_edges walks each member's out-edges; it must match a brute-force scan
# of every graph edge (the original implementation) on any input.


def test_cycle_edges_matches_full_edge_scan():
    from repowise.core.analysis.health.refactoring.graph_signals import (
        _CYCLE_EDGE_TYPES,
        cycle_edges,
    )

    g = _import_graph(
        [
            ("a.py", "b.py"),
            ("b.py", "c.py"),
            ("c.py", "a.py"),
            ("c.py", "d.py"),  # leaves the cycle
            ("x.py", "a.py"),  # enters the cycle from outside
        ]
    )
    g.add_edge("a.py", "a.py", edge_type="imports")  # self-loop, excluded
    g.add_edge("b.py", "a.py", edge_type="co_changes")  # non-cycle edge type
    g.add_edge("a.py", "c.py", edge_type="type_use")

    members = ("a.py", "b.py", "c.py", "ghost.py")  # a member absent from the graph

    def reference(graph, mem):
        member_set = set(mem)
        edges = []
        for u, v, data in graph.edges(data=True):
            if u == v or u not in member_set or v not in member_set:
                continue
            if data.get("edge_type") in _CYCLE_EDGE_TYPES:
                edges.append((u, v))
        return sorted(set(edges))

    assert cycle_edges(g, members) == reference(g, members)
    assert cycle_edges(g, ()) == []
    assert cycle_edges(None, members) == []
