"""Unit tests for the shared implicit-scope scan.

The three language bindings each have their own suite; these pin the rules
that used to live three times and now live once, so a change to the shared
driver fails here rather than showing up as an edge diff on one language.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.ingestion.languages.scope_scan import (
    FileScope,
    ScopeTier,
    emit_scope_edges,
)

_EMPTY: frozenset[str] = frozenset()


def _graph(*paths: str) -> nx.DiGraph:
    g = nx.DiGraph()
    for p in paths:
        g.add_node(p, node_type="file")
    return g


def _one_tier(index: dict[str, list[str]], hint: str = "same_scope") -> FileScope:
    return FileScope(tiers=(ScopeTier(hint=hint, lookup=lambda i: index.get(i, ())),))


class TestUniqueDeclarerRule:
    def test_single_declarer_produces_an_edge_naming_the_identifier(self) -> None:
        g = _graph("a.x", "b.x")
        added = emit_scope_edges(
            g,
            [("a.x", "Widget w; Widget v;")],
            lambda p, t: _one_tier({"Widget": ["b.x"]}),
            skip_names=_EMPTY,
        )
        assert added == 1
        edge = g["a.x"]["b.x"]
        assert edge["edge_type"] == "imports"
        assert edge["hint_source"] == "same_scope"
        # Identifiers are deduplicated and sorted, not counted.
        assert edge["imported_names"] == ["Widget"]

    def test_two_declarers_produce_no_edge_to_anyone(self) -> None:
        g = _graph("a.x", "b.x", "c.x")
        added = emit_scope_edges(
            g,
            [("a.x", "Widget w;")],
            lambda p, t: _one_tier({"Widget": ["b.x", "c.x"]}),
            skip_names=_EMPTY,
        )
        assert added == 0

    def test_self_declaration_produces_no_edge(self) -> None:
        g = _graph("a.x")
        added = emit_scope_edges(
            g,
            [("a.x", "Widget w;")],
            lambda p, t: _one_tier({"Widget": ["a.x"]}),
            skip_names=_EMPTY,
        )
        assert added == 0

    def test_lowercase_identifiers_are_never_candidates(self) -> None:
        g = _graph("a.x", "b.x")
        added = emit_scope_edges(
            g,
            [("a.x", "widget w;")],
            lambda p, t: _one_tier({"widget": ["b.x"]}),
            skip_names=_EMPTY,
        )
        assert added == 0


class TestTierOrder:
    def _two_tiers(self, near: dict, far: dict) -> FileScope:
        return FileScope(
            tiers=(
                ScopeTier(hint="near", lookup=lambda i: near.get(i, ())),
                ScopeTier(hint="far", lookup=lambda i: far.get(i, ())),
            )
        )

    def test_nearer_scope_wins(self) -> None:
        g = _graph("a.x", "near.x", "far.x")
        emit_scope_edges(
            g,
            [("a.x", "Widget w;")],
            lambda p, t: self._two_tiers({"Widget": ["near.x"]}, {"Widget": ["far.x"]}),
            skip_names=_EMPTY,
        )
        assert g.has_edge("a.x", "near.x")
        assert not g.has_edge("a.x", "far.x")
        assert g["a.x"]["near.x"]["hint_source"] == "near"

    def test_a_farther_scope_answers_what_the_nearer_cannot_see(self) -> None:
        g = _graph("a.x", "far.x")
        emit_scope_edges(
            g,
            [("a.x", "Widget w;")],
            lambda p, t: self._two_tiers({}, {"Widget": ["far.x"]}),
            skip_names=_EMPTY,
        )
        assert g["a.x"]["far.x"]["hint_source"] == "far"

    def test_ambiguity_in_the_nearer_scope_is_terminal(self) -> None:
        # Visible-but-ambiguous nearby is not a reference to something far
        # away: the search stops rather than falling through to the next tier.
        g = _graph("a.x", "n1.x", "n2.x", "far.x")
        added = emit_scope_edges(
            g,
            [("a.x", "Widget w;")],
            lambda p, t: self._two_tiers(
                {"Widget": ["n1.x", "n2.x"]}, {"Widget": ["far.x"]}
            ),
            skip_names=_EMPTY,
        )
        assert added == 0

    def test_one_target_carries_the_hint_of_whichever_tier_answered_first(self) -> None:
        # Alphabetical identifier order decides, and both names ride the edge.
        g = _graph("a.x", "t.x")
        emit_scope_edges(
            g,
            [("a.x", "Alpha a; Beta b;")],
            lambda p, t: self._two_tiers({"Alpha": ["t.x"]}, {"Beta": ["t.x"]}),
            skip_names=_EMPTY,
        )
        edge = g["a.x"]["t.x"]
        assert edge["hint_source"] == "near"
        assert edge["imported_names"] == ["Alpha", "Beta"]


class TestSuppression:
    def test_skip_names_are_never_looked_up(self) -> None:
        g = _graph("a.x", "b.x")
        added = emit_scope_edges(
            g,
            [("a.x", "String s;")],
            lambda p, t: _one_tier({"String": ["b.x"]}),
            skip_names=frozenset({"String"}),
        )
        assert added == 0

    def test_a_shadowed_name_resolves_elsewhere(self) -> None:
        g = _graph("a.x", "b.x")
        added = emit_scope_edges(
            g,
            [("a.x", "Widget w;")],
            lambda p, t: FileScope(
                tiers=(ScopeTier(hint="h", lookup=lambda i: {"Widget": ["b.x"]}.get(i, ())),),
                shadowed=frozenset({"Widget"}),
            ),
            skip_names=_EMPTY,
        )
        assert added == 0

    def test_a_file_with_no_scope_is_skipped(self) -> None:
        g = _graph("a.x", "b.x")
        added = emit_scope_edges(
            g, [("a.x", "Widget w;")], lambda p, t: None, skip_names=_EMPTY
        )
        assert added == 0


class TestEmission:
    def test_an_existing_edge_is_left_alone(self) -> None:
        g = _graph("a.x", "b.x")
        g.add_edge("a.x", "b.x", edge_type="imports", imported_names=["real"])
        added = emit_scope_edges(
            g,
            [("a.x", "Widget w;")],
            lambda p, t: _one_tier({"Widget": ["b.x"]}),
            skip_names=_EMPTY,
        )
        assert added == 0
        assert g["a.x"]["b.x"]["imported_names"] == ["real"]
        assert "hint_source" not in g["a.x"]["b.x"]

    def test_a_target_outside_the_graph_produces_no_edge(self) -> None:
        g = _graph("a.x")
        added = emit_scope_edges(
            g,
            [("a.x", "Widget w;")],
            lambda p, t: _one_tier({"Widget": ["gone.x"]}),
            skip_names=_EMPTY,
        )
        assert added == 0
