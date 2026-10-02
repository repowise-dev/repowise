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


_PHP_IDENTITY = (
    "<?php\nnamespace App\\Identity;\nuse App\\Validation\\HostValidator;\n"
    "class HostIdentity { public function check() { return new HostValidator(); } }\n"
)


def _php_break_cycles(tmp_path, validator_src: str) -> list:
    from datetime import datetime

    from repowise.core.ingestion.graph import GraphBuilder
    from repowise.core.ingestion.models import FileInfo
    from repowise.core.ingestion.parser import ASTParser

    files = {
        "src/Identity/HostIdentity.php": _PHP_IDENTITY,
        "src/Validation/HostValidator.php": validator_src,
    }
    builder, parser = GraphBuilder(), ASTParser()
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        info = FileInfo(
            path=rel,
            abs_path=str(path),
            language="php",
            size_bytes=len(text),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        builder.add_file(parser.parse_file(info, text.encode()))
    return _detect(builder.build(), "src/Identity/HostIdentity.php")


def test_php_docblock_only_reference_does_not_close_a_cycle(tmp_path):
    # HostValidator names HostIdentity only in comments; no code depends on it.
    validator = (
        "<?php\nnamespace App\\Validation;\n"
        "/** Validates a host, see \\App\\Identity\\HostIdentity. */\n"
        "class HostValidator {\n"
        "    /** @param \\App\\Identity\\HostIdentity $host */\n"
        "    public function ok($host) { return true; } // not HostIdentity-specific\n"
        "}\n"
    )
    assert _php_break_cycles(tmp_path, validator) == []


def test_php_use_both_ways_is_a_cycle(tmp_path):
    validator = (
        "<?php\nnamespace App\\Validation;\nuse App\\Identity\\HostIdentity;\n"
        "class HostValidator { public function ok(HostIdentity $host) { return true; } }\n"
    )
    out = _php_break_cycles(tmp_path, validator)
    assert len(out) == 1
    assert out[0].plan["cycle"] == [
        "src/Identity/HostIdentity.php",
        "src/Validation/HostValidator.php",
    ]


def _detect_lang(g: nx.DiGraph, file_path: str, language: str) -> list:
    idx = build_file_scc_index(g)
    ctx = RefactoringContext(
        file_path=file_path, language=language, nloc=50, graph=g, file_scc=idx.get(file_path)
    )
    return [s for s in detect_refactorings(ctx) if s.refactoring_type == "break_cycle"]


def test_same_directory_java_cycle_is_kept_but_demoted():
    a, b = "src/com/acme/Project.java", "src/com/acme/User.java"
    out = _detect_lang(_import_graph([(a, b), (b, a)]), a, "java")
    assert len(out) == 1
    assert out[0].confidence == "low"
    assert out[0].evidence["idiom"] == out[0].plan["idiom"] == "same_directory"
    assert out[0].target_symbol.startswith("cycle[2] (same directory, idiomatic): ")


def test_cross_package_java_cycle_is_not_demoted():
    a, b = "src/com/acme/api/Client.java", "src/com/acme/core/Engine.java"
    out = _detect_lang(_import_graph([(a, b), (b, a)]), a, "java")
    assert out[0].confidence == "high"
    assert "idiom" not in out[0].evidence


def test_same_directory_python_cycle_is_not_demoted():
    # A Python import cycle fails at import time; it is never an idiom.
    out = _detect_lang(_import_graph([("pkg/a.py", "pkg/b.py"), ("pkg/b.py", "pkg/a.py")]), "pkg/a.py", "python")
    assert out[0].confidence == "high"
