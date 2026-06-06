"""Tests for KnowledgeGraphContext per-file lookups."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.generation.kg_context import KGFileContext, KnowledgeGraphContext


@pytest.fixture
def sample_kg_json(tmp_path):
    """Create a minimal knowledge-graph.json fixture with filesystem stubs."""
    kg = {
        "version": "1.0.0",
        "project": {"name": "test"},
        "nodes": [
            {"id": "file:src/main.py", "type": "file", "filePath": "src/main.py",
             "summary": "Entry point", "tags": ["cli", "entry_point"], "complexity": "simple"},
            {"id": "file:src/core.py", "type": "file", "filePath": "src/core.py",
             "summary": "Core logic", "tags": ["core"], "complexity": "complex"},
            {"id": "file:src/utils.py", "type": "file", "filePath": "src/utils.py",
             "summary": "", "tags": [], "complexity": "simple"},
        ],
        "edges": [
            {"source": "file:src/main.py", "target": "file:src/core.py",
             "type": "imports", "direction": "forward", "weight": 1.0},
            {"source": "file:src/core.py", "target": "file:src/utils.py",
             "type": "imports", "direction": "forward", "weight": 1.0},
        ],
        "layers": [
            {"id": "layer:cli", "name": "CLI", "description": "Command line interface",
             "nodeIds": ["file:src/main.py"]},
            {"id": "layer:core", "name": "Core", "description": "Core business logic",
             "nodeIds": ["file:src/core.py", "file:src/utils.py"]},
        ],
        "tour": [
            {"order": 1, "title": "Start Here",
             "description": "Begin with the CLI entry point.",
             "nodeIds": ["file:src/main.py"]},
        ],
    }
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").touch()
    (tmp_path / "src" / "core.py").touch()
    (tmp_path / "src" / "utils.py").touch()
    kg_path = tmp_path / ".repowise" / "knowledge-graph.json"
    kg_path.parent.mkdir()
    kg_path.write_text(json.dumps(kg))
    return kg_path


class TestLoad:
    def test_load_and_available(self, sample_kg_json):
        ctx = KnowledgeGraphContext(sample_kg_json)
        assert ctx.available

    def test_nonexistent_path(self):
        ctx = KnowledgeGraphContext(Path("/nonexistent/path.json"))
        assert not ctx.available

    def test_none_path(self):
        ctx = KnowledgeGraphContext(None)
        assert not ctx.available

    def test_malformed_json(self, tmp_path):
        bad_path = tmp_path / ".repowise" / "knowledge-graph.json"
        bad_path.parent.mkdir()
        bad_path.write_text("{{{invalid json")
        ctx = KnowledgeGraphContext(bad_path)
        assert not ctx.available


class TestGetFileContext:
    def test_entry_point_role(self, sample_kg_json):
        ctx = KnowledgeGraphContext(sample_kg_json)
        fc = ctx.get_file_context("src/main.py")
        assert fc is not None
        assert fc.layer_name == "CLI"
        assert fc.role == "entry_point"

    def test_edge_connector_role(self, sample_kg_json):
        ctx = KnowledgeGraphContext(sample_kg_json)
        fc = ctx.get_file_context("src/core.py")
        assert fc is not None
        assert fc.role == "edge_connector"

    def test_internal_role(self, sample_kg_json):
        ctx = KnowledgeGraphContext(sample_kg_json)
        fc = ctx.get_file_context("src/utils.py")
        assert fc is not None
        assert fc.role == "internal"

    def test_tour_step(self, sample_kg_json):
        ctx = KnowledgeGraphContext(sample_kg_json)
        fc = ctx.get_file_context("src/main.py")
        assert fc.tour_step is not None
        assert fc.tour_step["order"] == 1
        assert fc.tour_step["title"] == "Start Here"

    def test_no_tour_step(self, sample_kg_json):
        ctx = KnowledgeGraphContext(sample_kg_json)
        fc = ctx.get_file_context("src/core.py")
        assert fc.tour_step is None

    def test_tags(self, sample_kg_json):
        ctx = KnowledgeGraphContext(sample_kg_json)
        fc = ctx.get_file_context("src/main.py")
        assert "cli" in fc.tags
        assert "entry_point" in fc.tags

    def test_node_summary(self, sample_kg_json):
        ctx = KnowledgeGraphContext(sample_kg_json)
        fc = ctx.get_file_context("src/main.py")
        assert fc.node_summary == "Entry point"

    def test_neighbors(self, sample_kg_json):
        ctx = KnowledgeGraphContext(sample_kg_json)
        fc = ctx.get_file_context("src/main.py")
        assert len(fc.neighbors) == 1
        assert fc.neighbors[0]["path"] == "src/core.py"
        assert fc.neighbors[0]["same_layer"] is False
        assert fc.neighbors[0]["relationship"] == "imports"

    def test_cross_layer_neighbor(self, sample_kg_json):
        ctx = KnowledgeGraphContext(sample_kg_json)
        fc = ctx.get_file_context("src/core.py")
        neighbor_paths = {n["path"] for n in fc.neighbors}
        assert "src/main.py" in neighbor_paths
        assert "src/utils.py" in neighbor_paths

    def test_missing_file(self, sample_kg_json):
        ctx = KnowledgeGraphContext(sample_kg_json)
        assert ctx.get_file_context("nonexistent.py") is None

    def test_unavailable_returns_none(self):
        ctx = KnowledgeGraphContext(None)
        assert ctx.get_file_context("anything.py") is None


class TestTourValidation:
    def test_deleted_file_excluded_from_tour(self, tmp_path):
        kg = {
            "nodes": [{"id": "file:exists.py", "filePath": "exists.py"}],
            "edges": [],
            "layers": [{"id": "layer:x", "name": "X", "nodeIds": ["file:exists.py", "file:deleted.py"]}],
            "tour": [
                {"order": 1, "title": "Step 1", "description": "Y",
                 "nodeIds": ["file:exists.py", "file:deleted.py"]},
            ],
        }
        (tmp_path / "exists.py").touch()
        kg_path = tmp_path / ".repowise" / "knowledge-graph.json"
        kg_path.parent.mkdir()
        kg_path.write_text(json.dumps(kg))

        ctx = KnowledgeGraphContext(kg_path)
        assert ctx.available
        fc_exists = ctx.get_file_context("exists.py")
        assert fc_exists is not None
        assert fc_exists.tour_step is not None
        fc_deleted = ctx.get_file_context("deleted.py")
        assert fc_deleted is not None  # still in layer
        assert fc_deleted.tour_step is None  # excluded from tour


class TestLayers:
    def test_get_layers(self, sample_kg_json):
        ctx = KnowledgeGraphContext(sample_kg_json)
        layers = ctx.get_layers()
        assert len(layers) == 2
        names = {l["name"] for l in layers}
        assert "CLI" in names
        assert "Core" in names

    def test_get_layers_unavailable(self):
        ctx = KnowledgeGraphContext(None)
        assert ctx.get_layers() == []


class TestInterLayerEdges:
    def test_deps_out(self, sample_kg_json):
        ctx = KnowledgeGraphContext(sample_kg_json)
        cli_layer = next(l for l in ctx.get_layers() if l["name"] == "CLI")
        deps_out, deps_in = ctx.get_inter_layer_edges(cli_layer)
        assert len(deps_out) == 1
        assert deps_out[0]["target_layer"] == "Core"
        assert deps_out[0]["edge_count"] == 1

    def test_deps_in(self, sample_kg_json):
        ctx = KnowledgeGraphContext(sample_kg_json)
        core_layer = next(l for l in ctx.get_layers() if l["name"] == "Core")
        deps_out, deps_in = ctx.get_inter_layer_edges(core_layer)
        assert len(deps_in) == 1
        assert deps_in[0]["source_layer"] == "CLI"

    def test_no_cross_layer_edges(self, tmp_path):
        kg = {
            "nodes": [{"id": "file:a.py", "filePath": "a.py"}],
            "edges": [],
            "layers": [{"id": "layer:solo", "name": "Solo", "nodeIds": ["file:a.py"]}],
            "tour": [],
        }
        (tmp_path / "a.py").touch()
        kg_path = tmp_path / ".repowise" / "knowledge-graph.json"
        kg_path.parent.mkdir()
        kg_path.write_text(json.dumps(kg))

        ctx = KnowledgeGraphContext(kg_path)
        deps_out, deps_in = ctx.get_inter_layer_edges(ctx.get_layers()[0])
        assert deps_out == []
        assert deps_in == []


class TestCuratedTourShape:
    """The curated export's tour steps carry target_path + reason (no nodeIds)."""

    @pytest.fixture
    def curated_kg_json(self, tmp_path):
        kg = {
            "version": "1.0.0",
            "project": {"name": "test", "graph_mode": "flow"},
            "nodes": [
                {"id": "file:src/main.py", "type": "file", "filePath": "src/main.py"},
            ],
            "edges": [],
            "layers": [
                {"id": "layer:app", "name": "Application",
                 "nodeIds": ["file:src/main.py"]},
            ],
            "tour": [
                {"order": 1, "target_path": "src/main.py", "page_type": "file_page",
                 "title": "main.py", "depth": 1, "kind": "code",
                 "reason": "An entry point — execution and imports fan out from here.",
                 "layer_id": "layer:app"},
            ],
        }
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").touch()
        kg_path = tmp_path / ".repowise" / "knowledge-graph.json"
        kg_path.parent.mkdir()
        kg_path.write_text(json.dumps(kg))
        return kg_path

    def test_target_path_steps_map_to_files(self, curated_kg_json):
        ctx = KnowledgeGraphContext(curated_kg_json)
        fc = ctx.get_file_context("src/main.py")
        assert fc is not None and fc.tour_step is not None
        assert fc.tour_step["order"] == 1

    def test_tour_step_description_falls_back_to_reason(self, curated_kg_json):
        ctx = KnowledgeGraphContext(curated_kg_json)
        fc = ctx.get_file_context("src/main.py")
        assert fc.tour_step["description"].startswith("An entry point")

    def test_get_graph_mode(self, curated_kg_json):
        ctx = KnowledgeGraphContext(curated_kg_json)
        assert ctx.get_graph_mode() == "flow"

    def test_graph_mode_absent_on_uncurated(self, sample_kg_json):
        ctx = KnowledgeGraphContext(sample_kg_json)
        assert ctx.get_graph_mode() is None

    def test_graph_mode_unavailable(self):
        ctx = KnowledgeGraphContext(None)
        assert ctx.get_graph_mode() is None
