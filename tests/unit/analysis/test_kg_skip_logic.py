"""Tests for KG fingerprint-based skip logic (Phase 12)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.analysis.knowledge_graph import (
    KnowledgeGraphResult,
    compute_kg_fingerprint,
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
    """Tests the skip decision using KnowledgeGraphResult.from_file + fingerprint comparison."""

    def test_skip_when_fingerprint_matches(self, tmp_path):
        """Simulate what _run_ingestion does: if existing fingerprint matches,
        load from file instead of building."""
        kg = KnowledgeGraphResult(
            nodes=[{"id": "file:a.py"}],
            layers=[{"id": "layer:core", "name": "Core", "nodeIds": ["file:a.py"]}],
            tour=[{"order": 1, "title": "Start", "nodeIds": ["file:a.py"]}],
        )
        kg_path = tmp_path / ".repowise" / "knowledge-graph.json"
        kg_path.parent.mkdir(parents=True)
        kg_path.write_text(json.dumps(kg.to_dict()), encoding="utf-8")

        existing_fp = "abc123"
        new_fp = "abc123"  # same

        result = None
        if existing_fp and existing_fp == new_fp and kg_path.exists():
            result = KnowledgeGraphResult.from_file(kg_path)
            if result is not None:
                result.fingerprint = new_fp

        assert result is not None
        assert result.fingerprint == "abc123"
        assert len(result.nodes) == 1

    def test_regenerate_when_fingerprint_differs(self, tmp_path):
        kg_path = tmp_path / ".repowise" / "knowledge-graph.json"
        kg_path.parent.mkdir(parents=True)
        kg_path.write_text(json.dumps({"nodes": [], "layers": []}), encoding="utf-8")

        existing_fp = "abc123"
        new_fp = "def456"  # different

        result = None
        if existing_fp and existing_fp == new_fp and kg_path.exists():
            result = KnowledgeGraphResult.from_file(kg_path)

        assert result is None  # would proceed to build_knowledge_graph_skeleton

    def test_force_bypasses_fingerprint(self, tmp_path):
        """--force sets existing_kg_fingerprint to None, so skip never triggers."""
        kg_path = tmp_path / ".repowise" / "knowledge-graph.json"
        kg_path.parent.mkdir(parents=True)
        kg_path.write_text(json.dumps({"nodes": [{"id": "file:a.py"}]}), encoding="utf-8")

        existing_fp = None  # force=True → fingerprint not passed
        new_fp = "abc123"

        result = None
        if existing_fp and existing_fp == new_fp and kg_path.exists():
            result = KnowledgeGraphResult.from_file(kg_path)

        assert result is None  # would proceed to build

    def test_skip_fails_gracefully_if_file_missing(self, tmp_path):
        existing_fp = "abc123"
        new_fp = "abc123"
        kg_path = tmp_path / ".repowise" / "knowledge-graph.json"

        result = None
        if existing_fp and existing_fp == new_fp and kg_path.exists():
            result = KnowledgeGraphResult.from_file(kg_path)

        assert result is None  # file doesn't exist, so condition fails

    def test_skip_fails_gracefully_if_file_corrupt(self, tmp_path):
        existing_fp = "abc123"
        new_fp = "abc123"
        kg_path = tmp_path / ".repowise" / "knowledge-graph.json"
        kg_path.parent.mkdir(parents=True)
        kg_path.write_text("corrupt", encoding="utf-8")

        result = None
        if existing_fp and existing_fp == new_fp and kg_path.exists():
            result = KnowledgeGraphResult.from_file(kg_path)

        assert result is None  # from_file returns None for corrupt JSON
