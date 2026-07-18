"""Tests for layer_page generation (Phase 7)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from repowise.core.generation.context_assembler import LayerPageContext
from repowise.core.generation.models import GENERATION_LEVELS, PageType


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestLayerPageModel:
    def test_layer_page_in_page_type(self):
        assert "layer_page" in PageType.__args__

    def test_layer_page_generation_level(self):
        assert GENERATION_LEVELS["layer_page"] == 5

    def test_layer_page_after_module_before_overview(self):
        assert GENERATION_LEVELS["layer_page"] > GENERATION_LEVELS["module_page"]
        assert GENERATION_LEVELS["layer_page"] < GENERATION_LEVELS["repo_overview"]


# ---------------------------------------------------------------------------
# LayerPageContext tests
# ---------------------------------------------------------------------------


class TestLayerPageContext:
    def test_basic_construction(self):
        ctx = LayerPageContext(
            layer_name="Core Pipeline",
            layer_description="Central data processing",
            file_count=10,
        )
        assert ctx.layer_name == "Core Pipeline"
        assert ctx.file_count == 10
        assert ctx.key_files == []
        assert ctx.deps_out == []
        assert ctx.deps_in == []
        assert ctx.tour_steps == []

    def test_full_construction(self):
        ctx = LayerPageContext(
            layer_name="Core",
            layer_description="Core logic",
            file_count=5,
            key_files=[{"path": "src/core.py", "role": "edge_connector", "summary": "Core module"}],
            deps_out=[{"target_layer": "API", "edge_count": 3}],
            deps_in=[{"source_layer": "CLI", "edge_count": 1}],
            tour_steps=[{"order": 1, "title": "Core", "description": "Explore core"}],
            entry_points=["src/main.py"],
            edge_connectors=["src/core.py"],
        )
        assert len(ctx.key_files) == 1
        assert ctx.key_files[0]["path"] == "src/core.py"
        assert len(ctx.deps_out) == 1
        assert len(ctx.deps_in) == 1
        assert len(ctx.tour_steps) == 1
        assert len(ctx.entry_points) == 1
        assert len(ctx.edge_connectors) == 1


# ---------------------------------------------------------------------------
# Template rendering tests
# ---------------------------------------------------------------------------


class TestLayerPageTemplate:
    @pytest.fixture
    def jinja_env(self):
        from jinja2 import Environment, FileSystemLoader

        template_dir = Path(__file__).resolve().parents[3] / "packages" / "core" / "src" / "repowise" / "core" / "generation" / "templates"
        return Environment(loader=FileSystemLoader(str(template_dir)))

    def test_renders_layer_name(self, jinja_env):
        tmpl = jinja_env.get_template("layer_page.j2")
        ctx = LayerPageContext(
            layer_name="Core Pipeline",
            layer_description="Handles data processing",
            file_count=8,
        )
        rendered = tmpl.render(ctx=ctx)
        assert "**Core Pipeline**" in rendered
        assert "**Files:** 8" in rendered
        assert "Handles data processing" in rendered

    def test_renders_key_files(self, jinja_env):
        tmpl = jinja_env.get_template("layer_page.j2")
        ctx = LayerPageContext(
            layer_name="Core",
            layer_description="",
            file_count=5,
            key_files=[
                {"path": "src/core.py", "role": "edge_connector", "summary": "Core module"},
                {"path": "src/utils.py", "role": "internal", "summary": ""},
            ],
        )
        rendered = tmpl.render(ctx=ctx)
        assert "## Key Components" in rendered
        assert "`src/core.py`" in rendered
        assert "*(edge connector)*" in rendered
        assert "`src/utils.py`" in rendered

    def test_renders_inter_layer_deps(self, jinja_env):
        tmpl = jinja_env.get_template("layer_page.j2")
        ctx = LayerPageContext(
            layer_name="Core",
            layer_description="",
            file_count=5,
            deps_out=[{"target_layer": "Persistence", "edge_count": 4}],
            deps_in=[{"source_layer": "CLI", "edge_count": 2}],
        )
        rendered = tmpl.render(ctx=ctx)
        assert "## Inter-Layer Dependencies" in rendered
        assert "**Persistence**" in rendered
        assert "4 imports" in rendered
        assert "**CLI**" in rendered
        assert "2 imports" in rendered

    def test_renders_tour_steps(self, jinja_env):
        tmpl = jinja_env.get_template("layer_page.j2")
        ctx = LayerPageContext(
            layer_name="Core",
            layer_description="",
            file_count=5,
            tour_steps=[
                {"order": 2, "title": "Core Logic", "description": "Dive into the core."},
            ],
        )
        rendered = tmpl.render(ctx=ctx)
        assert "## Codebase Tour References" in rendered
        assert "Step 2: Core Logic" in rendered

    def test_renders_entry_points_and_connectors(self, jinja_env):
        tmpl = jinja_env.get_template("layer_page.j2")
        ctx = LayerPageContext(
            layer_name="CLI",
            layer_description="",
            file_count=3,
            entry_points=["src/main.py"],
            edge_connectors=["src/api.py"],
        )
        rendered = tmpl.render(ctx=ctx)
        assert "## Entry Points" in rendered
        assert "`src/main.py`" in rendered
        assert "## Edge Connectors" in rendered
        assert "`src/api.py`" in rendered

    def test_no_optional_sections_when_empty(self, jinja_env):
        tmpl = jinja_env.get_template("layer_page.j2")
        ctx = LayerPageContext(
            layer_name="Tiny",
            layer_description="",
            file_count=3,
        )
        rendered = tmpl.render(ctx=ctx)
        assert "highest-importance files" not in rendered
        assert "## Inter-Layer Dependencies" not in rendered
        assert "## Codebase Tour References" not in rendered
        assert "## Entry Points\n" not in rendered
        assert "## Edge Connectors\n" not in rendered


# ---------------------------------------------------------------------------
# System prompt tests
# ---------------------------------------------------------------------------


class TestLayerPagePrompt:
    def test_prompt_exists(self):
        from repowise.core.generation.page_generator.prompts import SYSTEM_PROMPTS

        assert "layer_page" in SYSTEM_PROMPTS
        prompt = SYSTEM_PROMPTS["layer_page"]
        assert "layer" in prompt.lower()
        assert "## Overview" in prompt


# ---------------------------------------------------------------------------
# build_level5_coros tests
# ---------------------------------------------------------------------------


class TestBuildLevel5Coros:
    def _make_run(self, tmp_path, layers, nodes=None, edges=None, tour=None):
        """Build a minimal mock _GenerationRun with KG context."""
        from unittest.mock import MagicMock
        from repowise.core.generation.kg_context import KnowledgeGraphContext

        if nodes is None:
            nodes = []
            for layer in layers:
                for nid in layer.get("nodeIds", []):
                    if nid.startswith("file:"):
                        fp = nid[5:]
                        nodes.append({"id": nid, "filePath": fp})

        kg = {
            "nodes": nodes,
            "edges": edges or [],
            "layers": layers,
            "tour": tour or [],
        }
        # Create files on disk for tour validation
        for node in nodes:
            fp = node.get("filePath", "")
            if fp:
                full = tmp_path / fp
                full.parent.mkdir(parents=True, exist_ok=True)
                full.touch()

        kg_path = tmp_path / ".repowise" / "knowledge-graph.json"
        kg_path.parent.mkdir(exist_ok=True)
        kg_path.write_text(json.dumps(kg))

        run = MagicMock()
        run.kg_ctx = KnowledgeGraphContext(kg_path)
        run.pagerank = {}
        run.completed_page_summaries = {}
        run.completed_ids = set()
        run.gen = MagicMock()
        return run

    def test_generates_for_layer_with_enough_files(self, tmp_path):
        from repowise.core.generation.page_generator.levels import build_level5_coros

        layers = [
            {"id": "layer:core", "name": "Core", "description": "Core logic",
             "nodeIds": ["file:a.py", "file:b.py", "file:c.py"]},
        ]
        run = self._make_run(tmp_path, layers)
        coros = build_level5_coros(run)
        assert len(coros) == 1
        # Page keyed by the layer's stable slug id, not its display name.
        assert coros[0][0] == "layer_page:layer:core"

    def test_layer_context_carries_diagram_when_modules_exist(self, tmp_path):
        from unittest.mock import MagicMock

        from repowise.core.generation.kg_context import KnowledgeGraphContext
        from repowise.core.generation.page_generator.levels import build_level5_coros

        files_a = [f"core/a/f{i}.py" for i in range(4)]
        files_b = [f"core/b/f{i}.py" for i in range(3)]
        nodes = [{"id": f"file:{f}", "filePath": f} for f in files_a + files_b]
        edges = [{"source": f"file:{files_a[0]}", "target": f"file:{files_b[0]}",
                  "type": "imports"}]
        layers = [{"id": "layer:core", "name": "Core", "description": "",
                   "nodeIds": [f"file:{f}" for f in files_a + files_b]}]
        modules = [
            {"id": "module:core-a", "name": "core/a", "path": "core/a",
             "layerId": "layer:core", "nodeIds": [f"file:{f}" for f in files_a]},
            {"id": "module:core-b", "name": "core/b", "path": "core/b",
             "layerId": "layer:core", "nodeIds": [f"file:{f}" for f in files_b]},
        ]
        kg = {"nodes": nodes, "edges": edges, "layers": layers, "modules": modules, "tour": []}
        for n in nodes:
            full = tmp_path / n["filePath"]
            full.parent.mkdir(parents=True, exist_ok=True)
            full.touch()
        kg_path = tmp_path / ".repowise" / "knowledge-graph.json"
        kg_path.parent.mkdir(exist_ok=True)
        kg_path.write_text(json.dumps(kg))

        run = MagicMock()
        run.kg_ctx = KnowledgeGraphContext(kg_path)
        run.pagerank = {}
        run.completed_page_summaries = {}
        run.completed_ids = set()
        run.gen = MagicMock()

        coros = build_level5_coros(run)
        assert len(coros) == 1
        ctx = run.gen.generate_layer_page.call_args.args[0]
        assert ctx.diagram_mermaid.startswith("flowchart TD")
        assert "core/a" in ctx.diagram_mermaid

    def test_skips_small_layers(self, tmp_path):
        from repowise.core.generation.page_generator.levels import build_level5_coros

        layers = [
            {"id": "layer:tiny", "name": "Tiny",
             "nodeIds": ["file:a.py", "file:b.py"]},
        ]
        run = self._make_run(tmp_path, layers)
        coros = build_level5_coros(run)
        assert len(coros) == 0

    def test_no_layers_no_coros(self, tmp_path):
        from repowise.core.generation.page_generator.levels import build_level5_coros

        run = self._make_run(tmp_path, [])
        coros = build_level5_coros(run)
        assert len(coros) == 0

    def test_no_kg_returns_empty(self):
        from unittest.mock import MagicMock
        from repowise.core.generation.kg_context import KnowledgeGraphContext
        from repowise.core.generation.page_generator.levels import build_level5_coros

        run = MagicMock()
        run.kg_ctx = KnowledgeGraphContext(None)
        coros = build_level5_coros(run)
        assert coros == []

    def test_skips_completed_layers(self, tmp_path):
        from repowise.core.generation.page_generator.levels import build_level5_coros

        layers = [
            {"id": "layer:core", "name": "Core",
             "nodeIds": ["file:a.py", "file:b.py", "file:c.py"]},
        ]
        run = self._make_run(tmp_path, layers)
        run.completed_ids = {"layer_page:layer:core"}
        coros = build_level5_coros(run)
        assert len(coros) == 0

    def test_multiple_layers(self, tmp_path):
        from repowise.core.generation.page_generator.levels import build_level5_coros

        layers = [
            {"id": "layer:core", "name": "Core",
             "nodeIds": ["file:a.py", "file:b.py", "file:c.py"]},
            {"id": "layer:api", "name": "API",
             "nodeIds": ["file:d.py", "file:e.py", "file:f.py", "file:g.py"]},
            {"id": "layer:tiny", "name": "Tiny",
             "nodeIds": ["file:h.py"]},
        ]
        run = self._make_run(tmp_path, layers)
        coros = build_level5_coros(run)
        assert len(coros) == 2
        page_ids = {c[0] for c in coros}
        assert "layer_page:layer:core" in page_ids
        assert "layer_page:layer:api" in page_ids

    def test_page_key_uses_slug_not_display_name(self, tmp_path):
        """The page key derives from the stable slug id, never the mutable
        display name — a layer named "Task Queue Core" with id ``layer:queue``
        is keyed by ``layer:queue`` so an LLM rename can't churn the key."""
        from repowise.core.generation.page_generator.levels import build_level5_coros

        layers = [
            {"id": "layer:queue", "name": "Task Queue Core",
             "nodeIds": ["file:a.py", "file:b.py", "file:c.py"]},
        ]
        run = self._make_run(tmp_path, layers)
        coros = build_level5_coros(run)
        assert len(coros) == 1
        assert coros[0][0] == "layer_page:layer:queue"


# ---------------------------------------------------------------------------
# Slug identity stability under enrichment
# ---------------------------------------------------------------------------


class TestLayerIdStability:
    def test_enrichment_renames_name_not_id_so_page_key_is_stable(self, tmp_path):
        """The LLM layer-name enrichment mutates ``name`` only; the slug ``id``
        — and therefore the layer page key — is unchanged across the rename."""
        from repowise.core.generation.page_generator.levels import build_level5_coros

        node_ids = ["file:a.py", "file:b.py", "file:c.py"]

        def key_for(name: str) -> str:
            layers = [{"id": "layer:queue", "name": name, "nodeIds": node_ids}]
            run = self._make_run(tmp_path, layers)
            coros = build_level5_coros(run)
            assert len(coros) == 1
            return coros[0][0]

        # Heuristic name -> page key.
        before = key_for("Application")
        # Enrichment rewrites the display name; id (and key) must not move.
        after = key_for("Task Queue Core")
        assert before == after == "layer_page:layer:queue"

    # Reuse the level-5 fixture harness verbatim.
    _make_run = TestBuildLevel5Coros._make_run
