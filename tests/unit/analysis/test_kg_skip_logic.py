"""Tests for KG fingerprint-based skip logic (Phase 12)."""

from __future__ import annotations

import json

from repowise.core.analysis.knowledge_graph import (
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
