"""``add_co_change_edges``: the co-change records it reads are untrusted."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.co_change import parse_partners
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.pipeline.phases.git import label_co_change_structure


@pytest.fixture
def builder(tmp_path: Path) -> GraphBuilder:
    """A real builder holding two file nodes and nothing else."""
    gb = GraphBuilder(repo_path=tmp_path)
    gb._graph.add_nodes_from(("a.py", "b.py"))
    return gb


def _meta(partners: object) -> dict:
    return {"co_change_partners_json": json.dumps(partners)}


def test_a_bare_string_partner_does_not_crash_the_pass(builder: GraphBuilder) -> None:
    b = builder
    meta = {"a.py": _meta(["b.py", {"file_path": "b.py", "co_change_count": 9, "frequency": 9}])}
    assert b.add_co_change_edges(meta) == 1
    edge = b._graph.edges["a.py", "b.py"]
    assert edge["edge_type"] == "co_changes"
    assert edge["weight"] == 9.0


def test_a_non_numeric_weight_does_not_crash_the_pass(builder: GraphBuilder) -> None:
    b = builder
    meta = {"a.py": _meta([{"file_path": "b.py", "co_change_count": "lots", "frequency": 9}])}
    assert b.add_co_change_edges(meta) == 0


def test_the_pair_is_added_once_from_either_side(builder: GraphBuilder) -> None:
    b = builder
    meta = {
        "a.py": _meta([{"file_path": "b.py", "co_change_count": 9, "frequency": 9}]),
        "b.py": _meta([{"file_path": "a.py", "co_change_count": 9, "frequency": 9}]),
    }
    assert b.add_co_change_edges(meta) == 1


def test_a_pair_seen_once_is_not_an_edge(builder: GraphBuilder) -> None:
    """The gate counts shared commits, so a heavy but rare pair is still rare."""
    b = builder
    meta = {"a.py": _meta([{"file_path": "b.py", "co_change_count": 99, "frequency": 1}])}
    assert b.add_co_change_edges(meta) == 0


class TestStructuralLabel:
    """``label_co_change_structure`` over the same graph the edges come from."""

    def test_an_import_edge_corroborates_the_pair(self, builder: GraphBuilder) -> None:
        builder._graph.add_edge("a.py", "b.py", edge_type="imports")
        partners = {"a.py": [{"file_path": "b.py", "frequency": 9}]}
        assert builder.label_co_change_structure(partners) == 0
        assert partners["a.py"][0]["structural"] == "corroborated"

    def test_a_type_reference_corroborates_it_too(self, builder: GraphBuilder) -> None:
        """Six of the seven dependency edge types used to be invisible here, so
        a pair the graph plainly explained was reported as hidden."""
        builder._graph.add_edge("a.py", "b.py", edge_type="type_use")
        partners = {"a.py": [{"file_path": "b.py", "frequency": 9}]}
        assert builder.label_co_change_structure(partners) == 0
        assert partners["a.py"][0]["structural"] == "corroborated"

    def test_an_edge_the_other_way_still_corroborates(self, builder: GraphBuilder) -> None:
        builder._graph.add_edge("b.py", "a.py", edge_type="imports")
        partners = {"a.py": [{"file_path": "b.py", "frequency": 9}]}
        builder.label_co_change_structure(partners)
        assert partners["a.py"][0]["structural"] == "corroborated"

    def test_a_co_change_edge_does_not_corroborate_itself(self, builder: GraphBuilder) -> None:
        builder._graph.add_edge("a.py", "b.py", edge_type="co_changes")
        partners = {"a.py": [{"file_path": "b.py", "frequency": 9}]}
        assert builder.label_co_change_structure(partners) == 1
        assert partners["a.py"][0]["structural"] == "unexplained"

    def test_two_graph_nodes_with_no_edge_are_unexplained(self, builder: GraphBuilder) -> None:
        partners = {"a.py": [{"file_path": "b.py", "frequency": 9}]}
        assert builder.label_co_change_structure(partners) == 1
        assert partners["a.py"][0]["structural"] == "unexplained"

    def test_a_file_outside_the_graph_is_not_applicable(self, builder: GraphBuilder) -> None:
        """A lockfile is tracked and co-changes, but is never parsed, so it has
        no edge to find and its absence is not evidence of anything."""
        partners = {"a.py": [{"file_path": "uv.lock", "frequency": 40}]}
        assert builder.label_co_change_structure(partners) == 0
        assert partners["a.py"][0]["structural"] == "not_applicable"

    def test_both_sides_outside_the_graph_is_not_applicable(self, builder: GraphBuilder) -> None:
        partners = {"pyproject.toml": [{"file_path": "uv.lock", "frequency": 40}]}
        assert builder.label_co_change_structure(partners) == 0
        assert partners["pyproject.toml"][0]["structural"] == "not_applicable"

    def test_a_malformed_record_is_skipped_not_raised(self, builder: GraphBuilder) -> None:
        partners = {"a.py": ["b.py", {"nope": 1}, {"file_path": "b.py", "frequency": 9}]}
        assert builder.label_co_change_structure(partners) == 1

    def test_a_manifest_in_the_graph_is_still_not_applicable(
        self, builder: GraphBuilder
    ) -> None:
        """The bug this replaced: a manifest *is* a node, so membership said
        "unexplained" and dropped release plumbing into the findings segment.
        No resolver can emit an edge for TOML, so there is nothing to find."""
        builder._graph.add_node("pyproject.toml", language="toml")
        partners = {"a.py": [{"file_path": "pyproject.toml", "frequency": 40}]}
        assert builder.label_co_change_structure(partners) == 0
        assert partners["a.py"][0]["structural"] == "not_applicable"

    def test_a_doc_in_the_graph_is_not_applicable(self, builder: GraphBuilder) -> None:
        builder._graph.add_node("README.md", language="markdown")
        partners = {"a.py": [{"file_path": "README.md", "frequency": 40}]}
        assert builder.label_co_change_structure(partners) == 0
        assert partners["a.py"][0]["structural"] == "not_applicable"

    def test_a_language_with_a_resolver_stays_a_finding(self, builder: GraphBuilder) -> None:
        """The gate is "can a resolver emit an edge", not "is it code": HTML is
        markup, but it resolves asset references, so a missing one is real."""
        builder._graph.add_node("page.html", language="html")
        partners = {"a.py": [{"file_path": "page.html", "frequency": 9}]}
        assert builder.label_co_change_structure(partners) == 1
        assert partners["a.py"][0]["structural"] == "unexplained"

    def test_an_edge_outranks_the_language_gate(self, builder: GraphBuilder) -> None:
        """An edge that exists explains the pair whatever the language is."""
        builder._graph.add_node("page.html", language="markdown")
        builder._graph.add_edge("a.py", "page.html", edge_type="framework")
        partners = {"a.py": [{"file_path": "page.html", "frequency": 9}]}
        assert builder.label_co_change_structure(partners) == 0
        assert partners["a.py"][0]["structural"] == "corroborated"

    def test_the_corroborating_edge_names_itself(self, builder: GraphBuilder) -> None:
        builder._graph.add_edge("a.py", "b.py", edge_type="type_use")
        partners = {"a.py": [{"file_path": "b.py", "frequency": 9}]}
        builder.label_co_change_structure(partners)
        assert partners["a.py"][0]["dependency_kind"] == "type_use"

    def test_a_stale_kind_is_cleared_when_the_edge_goes(self, builder: GraphBuilder) -> None:
        """Records are relabelled in place across runs, so a kind left behind
        would claim a dependency the graph no longer has."""
        partners = {
            "a.py": [{"file_path": "b.py", "frequency": 9, "dependency_kind": "imports"}]
        }
        assert builder.label_co_change_structure(partners) == 1
        assert "dependency_kind" not in partners["a.py"][0]


class TestLabelPersistence:
    """``label_co_change_structure`` writes through the JSON column.

    The label is derived where the graph exists but consumed after a database
    round-trip, so the write-back is the part that can silently do nothing.
    """

    def test_the_label_survives_the_column_round_trip(self, builder: GraphBuilder) -> None:
        builder._graph.add_edge("a.py", "b.py", edge_type="framework")
        git_meta_map = {
            "a.py": _meta(
                [
                    {"file_path": "b.py", "co_change_count": 5.0, "frequency": 9},
                    {"file_path": "uv.lock", "co_change_count": 5.0, "frequency": 9},
                ]
            )
        }

        assert label_co_change_structure(builder, git_meta_map) == 0

        # Re-read the way every consumer does, not from the in-memory records.
        labels = {
            p.file_path: p.structural
            for p in parse_partners(git_meta_map["a.py"]["co_change_partners_json"])
        }
        assert labels == {"b.py": "corroborated", "uv.lock": "not_applicable"}

    def test_a_file_with_no_partners_is_left_alone(self, builder: GraphBuilder) -> None:
        git_meta_map = {"a.py": {"co_change_partners_json": None}}
        assert label_co_change_structure(builder, git_meta_map) == 0
        assert git_meta_map["a.py"]["co_change_partners_json"] is None

    def test_an_empty_graph_leaves_existing_labels_alone(self, tmp_path: Path) -> None:
        """A run that rehydrated no graph must not relabel everything as
        not-applicable, which would erase what an earlier run established."""
        empty = GraphBuilder(repo_path=tmp_path)
        partners = {"a.py": [{"file_path": "b.py", "frequency": 9, "structural": "unexplained"}]}
        assert empty.label_co_change_structure(partners) == 0
        assert partners["a.py"][0]["structural"] == "unexplained"
