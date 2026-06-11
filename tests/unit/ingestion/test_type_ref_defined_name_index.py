"""Equivalence tests for the type-ref defined-name index.

``resolve_type_refs`` used to locate a type's defining file by scanning
``graph.successors(candidate)`` for every (type reference, candidate file)
pair. It now precomputes one ``node -> {symbol names among successors}``
map per pass and answers lookups by set membership. These tests pin the
two formulations to each other:

* the index itself matches a per-node successor scan on graphs mixing
  file nodes, symbol nodes, symbol->symbol edges, and unnamed symbols;
* the find helpers return the same first-sorted-candidate answer as a
  reference implementation of the old scan loop;
* adding the kind of edges strategies emit mid-pass (file -> file
  ``type_use``) changes neither formulation, which is the invariant that
  makes the upfront build equivalent to live scanning.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.ingestion.type_ref_resolution import (
    _build_defined_name_index,
    _find_go_type_file,
    _find_jvm_type_file,
)


def _reference_scan(type_name: str, candidates, graph: nx.DiGraph) -> str | None:
    """The pre-index lookup loop, kept verbatim as the behavioral oracle."""
    for cand in sorted(candidates):
        if not graph.has_node(cand):
            continue
        for succ in graph.successors(cand):
            nd = graph.nodes.get(succ, {})
            if nd.get("node_type") == "symbol" and nd.get("name") == type_name:
                return cand
    return None


def _make_graph() -> nx.DiGraph:
    g = nx.DiGraph()
    # Files
    for f in ("a.go", "b.go", "pkg/c.go", "pkg/d.go", "no_symbols.go"):
        g.add_node(f, node_type="file", name=f)
    # Symbols (file -> symbol "defines" edges)
    defs = {
        "a.go": [("a.go::Writer", "Writer"), ("a.go::Reader", "Reader")],
        "b.go": [("b.go::Writer", "Writer"), ("b.go::Helper", "Helper")],
        "pkg/c.go": [("pkg/c.go::Config", "Config")],
        "pkg/d.go": [("pkg/d.go::unnamed", ""), ("pkg/d.go::Config", "Config")],
    }
    for f, symbols in defs.items():
        for sid, name in symbols:
            g.add_node(sid, node_type="symbol", name=name)
            g.add_edge(f, sid, edge_type="defines")
    # file -> file imports edge: target file node has a name attr but is
    # not a symbol; must never satisfy a lookup.
    g.add_edge("a.go", "pkg/c.go", edge_type="imports", imported_names=["Config"])
    # symbol -> symbol edge (has_method style); the method symbol must not
    # appear under the *file* keys it is not a direct successor of.
    g.add_node("a.go::Writer.Write", node_type="symbol", name="Write")
    g.add_edge("a.go::Writer", "a.go::Writer.Write", edge_type="has_method")
    return g


class TestIndexEquivalence:
    def test_index_matches_successor_scan_per_node(self) -> None:
        g = _make_graph()
        index = _build_defined_name_index(g)
        for node in g.nodes:
            expected = {
                d.get("name")
                for s in g.successors(node)
                if (d := g.nodes[s]).get("node_type") == "symbol" and d.get("name")
            }
            assert index.get(node, set()) == expected, node

    def test_unnamed_symbols_and_file_successors_excluded(self) -> None:
        g = _make_graph()
        index = _build_defined_name_index(g)
        # pkg/d.go defines an unnamed symbol and Config: only Config lands.
        assert index["pkg/d.go"] == {"Config"}
        # a.go's successors include the file node pkg/c.go (named "pkg/c.go")
        # via the imports edge; file successors never contribute.
        assert "pkg/c.go" not in index["a.go"]
        assert index["a.go"] == {"Writer", "Reader"}
        # A file defining nothing has no entry; lookups treat that as empty.
        assert "no_symbols.go" not in index

    def test_find_matches_reference_for_all_names_and_candidate_sets(self) -> None:
        g = _make_graph()
        index = _build_defined_name_index(g)
        candidate_sets = [
            {"a.go", "b.go"},
            {"b.go", "a.go", "pkg/c.go"},
            {"pkg/c.go", "pkg/d.go"},
            {"no_symbols.go", "b.go"},
            {"missing.go", "a.go"},  # candidate not in the graph
            set(),
        ]
        # Falsy names are outside the lookup domain: every strategy skips
        # them before calling a find helper (``if not name: continue``), so
        # the index drops unnamed symbols rather than matching "" the way a
        # raw attribute comparison would.
        names = ["Writer", "Reader", "Helper", "Config", "Write", "Nope"]
        for cands in candidate_sets:
            sorted_cands = sorted(cands)
            for name in names:
                expected = _reference_scan(name, cands, g)
                assert _find_go_type_file(name, sorted_cands, index) == expected, (
                    name,
                    cands,
                )
                assert _find_jvm_type_file(name, sorted_cands, index) == expected, (
                    name,
                    cands,
                )

    def test_first_sorted_candidate_wins_on_shadowed_name(self) -> None:
        g = _make_graph()
        index = _build_defined_name_index(g)
        # Writer is defined in both a.go and b.go: sorted order picks a.go,
        # exactly as the reference scan does.
        assert _find_go_type_file("Writer", sorted({"b.go", "a.go"}), index) == "a.go"
        assert _reference_scan("Writer", {"b.go", "a.go"}, g) == "a.go"

    def test_mid_pass_type_use_edges_do_not_invalidate_index(self) -> None:
        """Strategies only add file -> file ``type_use`` edges while running;
        neither formulation's answers change, so building the index upfront
        is equivalent to scanning live."""
        g = _make_graph()
        index = _build_defined_name_index(g)
        before = {
            name: _reference_scan(name, {"a.go", "b.go", "pkg/c.go"}, g)
            for name in ("Writer", "Config", "Helper")
        }
        g.add_edge(
            "b.go",
            "pkg/c.go",
            edge_type="type_use",
            confidence=0.8,
            type_uses=["Config"],
            imported_names=["Config"],
        )
        sorted_cands = sorted({"a.go", "b.go", "pkg/c.go"})
        for name, expected in before.items():
            assert _reference_scan(name, {"a.go", "b.go", "pkg/c.go"}, g) == expected
            assert _find_go_type_file(name, sorted_cands, index) == expected
