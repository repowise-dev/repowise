"""Tests for KG fingerprint-based skip logic (Phase 12)."""

from __future__ import annotations

import json
import logging

import pytest

from repowise.core.analysis import knowledge_graph
from repowise.core.analysis.knowledge_graph import (
    _KG_SCHEMA_VERSION,
    KnowledgeGraphResult,
    compute_kg_fingerprint,
    should_skip_kg_rebuild,
)

# ---------------------------------------------------------------------------
# KnowledgeGraphResult.from_file
# ---------------------------------------------------------------------------


class TestKGResultFromFile:
    def test_loads_valid_json(self, tmp_path):
        kg = KnowledgeGraphResult(
            project={"name": "test"},
            nodes=[{"id": "file:a.py", "filePath": "a.py"}],
            edges=[{"source": "file:a.py", "target": "file:b.py", "type": "imports"}],
            layers=[{"id": "layer:core", "name": "Core", "nodeIds": ["file:a.py"]}],
            tour=[{"order": 1, "title": "Start", "nodeIds": ["file:a.py"]}],
        )
        p = tmp_path / "kg.json"
        p.write_text(json.dumps(kg.to_dict()), encoding="utf-8")

        loaded = KnowledgeGraphResult.from_file(p)
        assert loaded is not None
        assert len(loaded.nodes) == 1
        assert len(loaded.edges) == 1
        assert len(loaded.layers) == 1
        assert len(loaded.tour) == 1
        assert loaded.project["name"] == "test"

    def test_returns_none_for_missing_file(self, tmp_path):
        p = tmp_path / "missing.json"
        assert KnowledgeGraphResult.from_file(p) is None

    def test_returns_none_for_invalid_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not valid json", encoding="utf-8")
        assert KnowledgeGraphResult.from_file(p) is None

    def test_handles_minimal_json(self, tmp_path):
        p = tmp_path / "kg.json"
        p.write_text("{}", encoding="utf-8")
        loaded = KnowledgeGraphResult.from_file(p)
        assert loaded is not None
        assert loaded.nodes == []
        assert loaded.layers == []
        assert loaded.tour == []

    def test_fingerprint_not_loaded(self, tmp_path):
        p = tmp_path / "kg.json"
        p.write_text(json.dumps({"nodes": [], "layers": []}), encoding="utf-8")
        loaded = KnowledgeGraphResult.from_file(p)
        assert loaded is not None
        assert loaded.fingerprint == ""

    def test_to_dict_carries_the_schema_version(self):
        assert KnowledgeGraphResult().to_dict()["schema_version"] == _KG_SCHEMA_VERSION

    def test_rejects_an_older_schema(self, tmp_path, caplog):
        p = tmp_path / "kg.json"
        p.write_text(
            json.dumps({"schema_version": _KG_SCHEMA_VERSION - 1, "nodes": [{"id": "file:a.py"}]}),
            encoding="utf-8",
        )
        with caplog.at_level(logging.INFO):
            assert KnowledgeGraphResult.from_file(p) is None
        assert "schema_version" in caplog.text

    def test_rejects_a_non_integer_schema_version(self, tmp_path):
        p = tmp_path / "kg.json"
        p.write_text(json.dumps({"schema_version": "1.0.0", "nodes": []}), encoding="utf-8")
        assert KnowledgeGraphResult.from_file(p) is None

    def test_rejects_a_boolean_schema_version(self, tmp_path):
        """``True`` is an int in Python, and ``True < 1`` is False, so it loaded."""
        p = tmp_path / "kg.json"
        p.write_text(json.dumps({"schema_version": True, "nodes": []}), encoding="utf-8")
        assert KnowledgeGraphResult.from_file(p) is None

    @pytest.mark.parametrize("payload", ["[]", '"x"', "5", "null", "true"])
    def test_rejects_valid_json_that_is_not_an_object(self, tmp_path, payload):
        """All five are valid JSON and all five are reachable from a hand edit.

        The loader called ``.get`` on whatever came back, so these raised
        AttributeError — which the orchestrator's except tuple does not name,
        so it escaped and killed the run. That is the failure the guard around
        this loader exists to stop.
        """
        p = tmp_path / "kg.json"
        p.write_text(payload, encoding="utf-8")
        assert KnowledgeGraphResult.from_file(p) is None

    def test_accepts_a_file_written_before_the_field_existed(self, tmp_path):
        """Unversioned files have the current shape, so they still load.

        The gate only bites on a future bump; rejecting today's artifacts
        would throw away curated layer names for no reason.
        """
        p = tmp_path / "kg.json"
        p.write_text(json.dumps({"nodes": [{"id": "file:a.py"}], "layers": []}), encoding="utf-8")
        loaded = KnowledgeGraphResult.from_file(p)
        assert loaded is not None
        assert len(loaded.nodes) == 1

    def test_accepts_a_newer_schema(self, tmp_path):
        """Forward compatibility is the writer's problem, not the reader's."""
        p = tmp_path / "kg.json"
        p.write_text(
            json.dumps({"schema_version": _KG_SCHEMA_VERSION + 1, "nodes": []}),
            encoding="utf-8",
        )
        assert KnowledgeGraphResult.from_file(p) is not None


# ---------------------------------------------------------------------------
# Fingerprint determinism
# ---------------------------------------------------------------------------


class TestFingerprintDeterminism:
    def _make_graph_builder(self, nodes, edges, communities):
        from unittest.mock import MagicMock

        import networkx as nx

        g = nx.DiGraph()
        for n in nodes:
            g.add_node(n)
        for s, t in edges:
            g.add_edge(s, t)

        gb = MagicMock()
        gb.graph.return_value = g
        gb.community_detection.return_value = communities
        return gb

    def test_same_graph_same_fingerprint(self):
        gb1 = self._make_graph_builder(
            ["a.py", "b.py"], [("a.py", "b.py")], {"a.py": 0, "b.py": 0}
        )
        gb2 = self._make_graph_builder(
            ["a.py", "b.py"], [("a.py", "b.py")], {"a.py": 0, "b.py": 0}
        )
        assert compute_kg_fingerprint(gb1) == compute_kg_fingerprint(gb2)

    def test_different_nodes_different_fingerprint(self):
        gb1 = self._make_graph_builder(
            ["a.py", "b.py"], [("a.py", "b.py")], {"a.py": 0, "b.py": 0}
        )
        gb2 = self._make_graph_builder(
            ["a.py", "b.py", "c.py"], [("a.py", "b.py")], {"a.py": 0, "b.py": 0, "c.py": 1}
        )
        assert compute_kg_fingerprint(gb1) != compute_kg_fingerprint(gb2)

    def test_different_edges_different_fingerprint(self):
        gb1 = self._make_graph_builder(
            ["a.py", "b.py"], [("a.py", "b.py")], {"a.py": 0, "b.py": 0}
        )
        gb2 = self._make_graph_builder(
            ["a.py", "b.py"], [], {"a.py": 0, "b.py": 0}
        )
        assert compute_kg_fingerprint(gb1) != compute_kg_fingerprint(gb2)

    def test_different_communities_different_fingerprint(self):
        gb1 = self._make_graph_builder(
            ["a.py", "b.py"], [], {"a.py": 0, "b.py": 0}
        )
        gb2 = self._make_graph_builder(
            ["a.py", "b.py"], [], {"a.py": 0, "b.py": 1}
        )
        assert compute_kg_fingerprint(gb1) != compute_kg_fingerprint(gb2)

    def test_builder_version_changes_the_fingerprint(self, monkeypatch):
        """The one input that is not a measurement of the graph.

        Every other test here varies the graph and expects the fingerprint to
        follow. This is the opposite case, and the one the skip logic could not
        express before: the graph is identical and the *builder* changed, which
        is what a release does when it widens ``_EDGE_TYPE_MAP`` or re-ranks
        entry points. Without this fold an existing store keeps the artifact a
        narrower builder wrote, for as long as its node and edge counts happen
        to hold still.
        """
        gb = self._make_graph_builder(["a.py", "b.py"], [("a.py", "b.py")], {"a.py": 0, "b.py": 0})
        before = compute_kg_fingerprint(gb)

        monkeypatch.setattr(knowledge_graph, "KG_BUILDER_VERSION", "test-next")
        assert compute_kg_fingerprint(gb) != before

    def test_builder_version_is_a_stable_constant(self):
        """Pins the fold to a constant, not to something that drifts per run.

        A value derived from a clock or from the module's own bytes would make
        every update rebuild the knowledge graph forever — the failure mode
        opposite to the one the constant exists to fix, and the more expensive
        of the two.
        """
        gb = self._make_graph_builder(["a.py"], [], {"a.py": 0})
        assert compute_kg_fingerprint(gb) == compute_kg_fingerprint(gb)
        assert isinstance(knowledge_graph.KG_BUILDER_VERSION, str)
        assert knowledge_graph.KG_BUILDER_VERSION


# ---------------------------------------------------------------------------
# Skip logic integration (unit-level)
# ---------------------------------------------------------------------------


class TestSkipLogicUnit:
    """Tests the skip decision via the extracted should_skip_kg_rebuild function."""

    def test_skip_when_fingerprint_matches(self, tmp_path):
        kg = KnowledgeGraphResult(
            nodes=[{"id": "file:a.py"}],
            layers=[{"id": "layer:core", "name": "Core", "nodeIds": ["file:a.py"]}],
            tour=[{"order": 1, "title": "Start", "nodeIds": ["file:a.py"]}],
        )
        kg_path = tmp_path / ".repowise" / "knowledge-graph.json"
        kg_path.parent.mkdir(parents=True)
        kg_path.write_text(json.dumps(kg.to_dict()), encoding="utf-8")

        assert should_skip_kg_rebuild("abc123", "abc123", kg_path) is True

        result = KnowledgeGraphResult.from_file(kg_path)
        assert result is not None
        assert len(result.nodes) == 1

    def test_regenerate_when_fingerprint_differs(self, tmp_path):
        kg_path = tmp_path / ".repowise" / "knowledge-graph.json"
        kg_path.parent.mkdir(parents=True)
        kg_path.write_text(json.dumps({"nodes": [], "layers": []}), encoding="utf-8")

        assert should_skip_kg_rebuild("abc123", "def456", kg_path) is False

    def test_force_bypasses_fingerprint(self, tmp_path):
        kg_path = tmp_path / ".repowise" / "knowledge-graph.json"
        kg_path.parent.mkdir(parents=True)
        kg_path.write_text(json.dumps({"nodes": [{"id": "file:a.py"}]}), encoding="utf-8")

        assert should_skip_kg_rebuild(None, "abc123", kg_path) is False

    def test_skip_fails_gracefully_if_file_missing(self, tmp_path):
        kg_path = tmp_path / ".repowise" / "knowledge-graph.json"
        assert should_skip_kg_rebuild("abc123", "abc123", kg_path) is False

    def test_skip_fails_gracefully_if_file_corrupt(self, tmp_path):
        kg_path = tmp_path / ".repowise" / "knowledge-graph.json"
        kg_path.parent.mkdir(parents=True)
        kg_path.write_text("corrupt", encoding="utf-8")

        assert should_skip_kg_rebuild("abc123", "abc123", kg_path) is True
        result = KnowledgeGraphResult.from_file(kg_path)
        assert result is None
