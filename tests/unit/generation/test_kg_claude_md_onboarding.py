"""Tests for KG-enriched CLAUDE.md and onboarding pages (Phase 10)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from jinja2 import Environment, FileSystemLoader

from repowise.core.generation.editor_files.data import (
    EditorFileData,
    KGLayerSummary,
    KGTourStepSummary,
)
from repowise.core.generation.onboarding.signals import OnboardingSignals
from repowise.core.generation.onboarding.subkinds.how_it_works import (
    HowItWorksContext,
    _build,
)
from repowise.core.generation.onboarding.subkinds.codebase_map import (
    CodebaseMapContext,
    _build as _build_codebase_map,
)
from repowise.core.generation.kg_context import KnowledgeGraphContext


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------

_TEMPLATE_DIR = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "core"
    / "src"
    / "repowise"
    / "core"
    / "generation"
    / "templates"
)


@pytest.fixture
def jinja_env():
    return Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)))


@pytest.fixture
def onboarding_env():
    return Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR / "onboarding")))


# ---------------------------------------------------------------------------
# CLAUDE.md template tests
# ---------------------------------------------------------------------------


class TestClaudeMdKGSection:
    def test_renders_layers_when_present(self, jinja_env):
        tmpl = jinja_env.get_template("claude_md.j2")
        data = EditorFileData(
            repo_name="test",
            indexed_at="2026-05-25",
            indexed_commit="abc123",
            architecture_summary="",
            kg_layers=[
                KGLayerSummary(name="CLI", file_count=10, description="Command-line interface"),
                KGLayerSummary(name="Core", file_count=25, description="Core business logic"),
            ],
        )
        rendered = tmpl.render(data=data)
        assert "### Architectural Layers" in rendered
        assert "CLI" in rendered
        assert "10" in rendered
        assert "Core" in rendered
        assert "25" in rendered

    def test_no_layers_section_without_data(self, jinja_env):
        tmpl = jinja_env.get_template("claude_md.j2")
        data = EditorFileData(
            repo_name="test",
            indexed_at="2026-05-25",
            indexed_commit="abc123",
            architecture_summary="",
        )
        rendered = tmpl.render(data=data)
        assert "Architectural Layers" not in rendered

    def test_renders_tour_when_present(self, jinja_env):
        tmpl = jinja_env.get_template("claude_md.j2")
        data = EditorFileData(
            repo_name="test",
            indexed_at="2026-05-25",
            indexed_commit="abc123",
            architecture_summary="",
            kg_layers=[KGLayerSummary(name="Core", file_count=5, description="Core")],
            kg_tour=[
                KGTourStepSummary(order=1, title="Entry Point", primary_file="src/main.py"),
                KGTourStepSummary(order=2, title="Graph Building", primary_file="src/graph.py"),
            ],
        )
        rendered = tmpl.render(data=data)
        assert "### Guided Tour (2 steps)" in rendered
        assert "**Entry Point**" in rendered
        assert "`src/main.py`" in rendered

    def test_key_modules_owner_column_dropped_when_no_owners(self, jinja_env):
        from repowise.core.generation.editor_files.data import KeyModule

        tmpl = jinja_env.get_template("claude_md.j2")
        data = EditorFileData(
            repo_name="test",
            indexed_at="2026-05-25",
            indexed_commit="abc123",
            architecture_summary="",
            key_modules=[
                KeyModule(name="src/api", purpose="API layer", file_count=4, owner=None),
                KeyModule(name="src/core", purpose="Core logic", file_count=9, owner=None),
            ],
        )
        rendered = tmpl.render(data=data)
        assert "| Module | Purpose |" in rendered
        assert "Owner" not in rendered

    def test_key_modules_owner_column_kept_when_any_owner(self, jinja_env):
        from repowise.core.generation.editor_files.data import KeyModule

        tmpl = jinja_env.get_template("claude_md.j2")
        data = EditorFileData(
            repo_name="test",
            indexed_at="2026-05-25",
            indexed_commit="abc123",
            architecture_summary="",
            key_modules=[
                KeyModule(name="src/api", purpose="API layer", file_count=4, owner="Alice"),
                KeyModule(name="src/core", purpose="Core logic", file_count=9, owner=None),
            ],
        )
        rendered = tmpl.render(data=data)
        assert "| Module | Purpose | Owner |" in rendered
        assert "Alice" in rendered

    def test_no_tour_section_without_data(self, jinja_env):
        tmpl = jinja_env.get_template("claude_md.j2")
        data = EditorFileData(
            repo_name="test",
            indexed_at="2026-05-25",
            indexed_commit="abc123",
            architecture_summary="",
            kg_layers=[KGLayerSummary(name="Core", file_count=5, description="Core")],
        )
        rendered = tmpl.render(data=data)
        assert "Guided Tour" not in rendered

    def test_tour_truncates_at_6(self, jinja_env):
        tmpl = jinja_env.get_template("claude_md.j2")
        steps = [
            KGTourStepSummary(order=i, title=f"Step {i}", primary_file=f"f{i}.py")
            for i in range(1, 10)
        ]
        data = EditorFileData(
            repo_name="test",
            indexed_at="2026-05-25",
            indexed_commit="abc123",
            architecture_summary="",
            kg_layers=[KGLayerSummary(name="Core", file_count=5, description="Core")],
            kg_tour=steps,
        )
        rendered = tmpl.render(data=data)
        assert "**Step 6**" in rendered
        assert "**Step 7**" not in rendered
        assert "3 more steps" in rendered


# ---------------------------------------------------------------------------
# OnboardingSignals tests
# ---------------------------------------------------------------------------


class TestOnboardingSignalsKG:
    def test_signals_carry_kg_data(self):
        signals = OnboardingSignals(
            repo_name="test",
            repo_structure=MagicMock(),
            parsed_files=(),
            source_map={},
            graph_builder=MagicMock(),
            pagerank={},
            betweenness={},
            community={},
            sccs=(),
            kg_layers=({"name": "Core", "nodeIds": ["file:a.py"]},),
            kg_tour_steps=({"order": 1, "title": "Start", "nodeIds": ["file:a.py"]},),
        )
        assert len(signals.kg_layers) == 1
        assert len(signals.kg_tour_steps) == 1

    def test_signals_default_empty(self):
        signals = OnboardingSignals(
            repo_name="test",
            repo_structure=MagicMock(),
            parsed_files=(),
            source_map={},
            graph_builder=MagicMock(),
            pagerank={},
            betweenness={},
            community={},
            sccs=(),
        )
        assert signals.kg_layers == ()
        assert signals.kg_tour_steps == ()


# ---------------------------------------------------------------------------
# HowItWorks + tour steps tests
# ---------------------------------------------------------------------------


class TestHowItWorksWithTour:
    def test_tour_steps_in_context(self):
        ctx = HowItWorksContext(
            repo_name="test",
            archetype="service",
            kg_tour_steps=[
                {"order": 1, "title": "Entry", "description": "Start here", "nodeIds": ["file:src/main.py"]},
            ],
        )
        assert len(ctx.kg_tour_steps) == 1

    def test_template_renders_tour(self, onboarding_env):
        tmpl = onboarding_env.get_template("how_it_works.j2")
        ctx = HowItWorksContext(
            repo_name="test",
            archetype="service",
            archetype_evidence=["framework detected"],
            kg_tour_steps=[
                {"order": 1, "title": "Entry Point", "description": "Start here", "nodeIds": ["file:src/main.py"]},
                {"order": 2, "title": "Processing", "description": "Core logic", "nodeIds": ["file:src/process.py"]},
            ],
        )
        rendered = tmpl.render(ctx=ctx)
        assert "## Guided Tour Steps" in rendered
        assert "Step 1: Entry Point" in rendered
        assert "`src/main.py`" in rendered
        assert "Step 2: Processing" in rendered

    def test_template_no_tour_no_flows(self, onboarding_env):
        tmpl = onboarding_env.get_template("how_it_works.j2")
        ctx = HowItWorksContext(
            repo_name="test",
            archetype="module",
            archetype_evidence=[],
        )
        rendered = tmpl.render(ctx=ctx)
        assert "Guided Tour Steps" not in rendered
        assert "No multi-hop execution flow was detected" in rendered

    def test_build_gate_passes_with_tour_only(self):
        signals = OnboardingSignals(
            repo_name="test",
            repo_structure=MagicMock(entry_points=[]),
            parsed_files=(),
            source_map={},
            graph_builder=MagicMock(execution_flows=MagicMock(return_value=None)),
            pagerank={},
            betweenness={},
            community={},
            sccs=(),
            external_systems=(),
            kg_tour_steps=({"order": 1, "title": "Start", "nodeIds": ["file:a.py"]},),
        )
        ctx = _build(signals)
        assert ctx is not None
        assert len(ctx.kg_tour_steps) == 1

    def test_build_gate_fails_module_no_tour_no_flows(self):
        repo_structure = MagicMock(entry_points=[], packages=[])
        signals = OnboardingSignals(
            repo_name="test",
            repo_structure=repo_structure,
            parsed_files=(),
            source_map={},
            graph_builder=MagicMock(execution_flows=MagicMock(return_value=None)),
            pagerank={},
            betweenness={},
            community={},
            sccs=(),
            external_systems=(),
        )
        ctx = _build(signals)
        assert ctx is None


# ---------------------------------------------------------------------------
# CodebaseMap + KG layers tests
# ---------------------------------------------------------------------------


class TestCodebaseMapWithLayers:
    def test_layers_in_context(self):
        ctx = CodebaseMapContext(
            repo_name="test",
            total_files=10,
            total_loc=500,
            kg_layers=[{"name": "Core", "description": "Core logic", "nodeIds": ["file:a.py"]}],
        )
        assert len(ctx.kg_layers) == 1

    def test_template_renders_layers(self, onboarding_env):
        tmpl = onboarding_env.get_template("codebase_map.j2")
        ctx = CodebaseMapContext(
            repo_name="test",
            total_files=10,
            total_loc=500,
            kg_layers=[
                {"name": "Core", "description": "Core business logic", "nodeIds": ["file:a.py", "file:b.py"]},
                {"name": "API", "description": "REST API layer", "nodeIds": ["file:c.py"]},
            ],
        )
        rendered = tmpl.render(ctx=ctx)
        assert "## Architectural Layers" in rendered
        assert "**Core**" in rendered
        assert "**API**" in rendered

    def test_template_no_layers_section(self, onboarding_env):
        tmpl = onboarding_env.get_template("codebase_map.j2")
        ctx = CodebaseMapContext(
            repo_name="test",
            total_files=10,
            total_loc=500,
        )
        rendered = tmpl.render(ctx=ctx)
        assert "Architectural Layers" not in rendered


# ---------------------------------------------------------------------------
# KnowledgeGraphContext.get_tour() tests
# ---------------------------------------------------------------------------


class TestKGContextGetTour:
    def test_get_tour_returns_tour(self, tmp_path):
        kg = {
            "nodes": [{"filePath": "a.py"}],
            "layers": [],
            "tour": [
                {"order": 1, "title": "Start", "nodeIds": ["file:a.py"]},
                {"order": 2, "title": "Next", "nodeIds": ["file:a.py"]},
            ],
            "edges": [],
        }
        (tmp_path / "a.py").touch()
        kg_dir = tmp_path / ".repowise"
        kg_dir.mkdir()
        kg_path = kg_dir / "knowledge-graph.json"
        kg_path.write_text(json.dumps(kg))
        ctx = KnowledgeGraphContext(kg_path)
        tour = ctx.get_tour()
        assert len(tour) == 2
        assert tour[0]["title"] == "Start"

    def test_get_tour_unavailable(self):
        ctx = KnowledgeGraphContext(None)
        assert ctx.get_tour() == []
