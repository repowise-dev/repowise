"""Tests for KG-enriched file page context assembly and template rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from repowise.core.generation.context_assembler import ContextAssembler, FilePageContext
from repowise.core.generation.kg_context import KGFileContext
from repowise.core.generation.models import GenerationConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> GenerationConfig:
    defaults = {
        "token_budget": 4000,
        "max_concurrency": 2,
        "large_file_source_pct": 0.5,
    }
    defaults.update(overrides)
    return GenerationConfig(**defaults)


def _make_parsed_file(
    path: str = "src/core.py",
    language: str = "python",
    symbols: list | None = None,
    imports: list | None = None,
) -> Any:
    fi = SimpleNamespace(
        path=path,
        language=language,
        is_api_contract=False,
        is_entry_point=False,
        is_test=False,
    )
    parsed = SimpleNamespace(
        file_info=fi,
        docstring="Core module",
        symbols=symbols or [],
        imports=imports or [],
        exports=[],
        parse_errors=[],
    )
    return parsed


class _FakeGraph:
    def __init__(self, edges: list[tuple[str, str]]):
        self._adj: dict[str, set[str]] = {}
        self._rev: dict[str, set[str]] = {}
        self._nodes: set[str] = set()
        self._edges = edges
        for s, t in edges:
            self._adj.setdefault(s, set()).add(t)
            self._rev.setdefault(t, set()).add(s)
            self._nodes.add(s)
            self._nodes.add(t)
        self.nodes = MagicMock()
        self.nodes.get = MagicMock(return_value={})
        self.nodes.__iter__ = MagicMock(return_value=iter(list(self._nodes)))

    def __contains__(self, path: str) -> bool:
        return path in self._nodes

    def out_edges(self, node: str, data: bool = False) -> list:
        targets = self._adj.get(node, set())
        if data:
            return [(node, t, {}) for t in targets]
        return [(node, t) for t in targets]

    def in_edges(self, node: str, data: bool = False) -> list:
        sources = self._rev.get(node, set())
        if data:
            return [(s, node, {}) for s in sources]
        return [(s, node) for s in sources]

    def successors(self, p: str) -> list[str]:
        return list(self._adj.get(p, []))

    def predecessors(self, p: str) -> list[str]:
        return list(self._rev.get(p, []))


def _make_graph(*edges: tuple[str, str]) -> _FakeGraph:
    return _FakeGraph(list(edges))


def _make_kg_file_context(**overrides: Any) -> KGFileContext:
    defaults = dict(
        layer_name="Core Pipeline",
        layer_description="Handles core data processing logic",
        role="edge_connector",
        neighbors=[
            {"path": "src/utils.py", "name": "utils.py", "same_layer": True, "relationship": "imports"},
            {"path": "src/api.py", "name": "api.py", "same_layer": False, "relationship": "imported_by"},
        ],
        tour_step={"order": 2, "title": "Core Logic", "description": "Explore the core processing pipeline."},
        tags=["core", "pipeline"],
        node_summary="Central processing module for data ingestion",
    )
    defaults.update(overrides)
    return KGFileContext(**defaults)


# ---------------------------------------------------------------------------
# Context assembly tests
# ---------------------------------------------------------------------------


class TestFilePageContextWithKG:
    def test_kg_fields_populated(self):
        assembler = ContextAssembler(_make_config())
        parsed = _make_parsed_file()
        graph = _make_graph(("src/core.py", "src/utils.py"))
        kg = _make_kg_file_context()

        ctx = assembler.assemble_file_page(
            parsed, graph, {"src/core.py": 0.5}, {}, {}, b"x = 1",
            kg_context=kg,
        )
        assert ctx.kg_layer_name == "Core Pipeline"
        assert ctx.kg_layer_description == "Handles core data processing logic"
        assert ctx.kg_layer_role == "edge_connector"
        assert len(ctx.kg_neighbors) == 2
        assert ctx.kg_tour_step is not None
        assert ctx.kg_tour_step["order"] == 2
        assert ctx.kg_tags == ["core", "pipeline"]
        assert ctx.kg_node_summary == "Central processing module for data ingestion"

    def test_kg_fields_empty_without_context(self):
        assembler = ContextAssembler(_make_config())
        parsed = _make_parsed_file()
        graph = _make_graph()

        ctx = assembler.assemble_file_page(
            parsed, graph, {}, {}, {}, b"x = 1",
        )
        assert ctx.kg_layer_name == ""
        assert ctx.kg_layer_role == ""
        assert ctx.kg_neighbors == []
        assert ctx.kg_tour_step is None
        assert ctx.kg_tags == []
        assert ctx.kg_node_summary == ""

    def test_kg_fields_with_none_context(self):
        assembler = ContextAssembler(_make_config())
        parsed = _make_parsed_file()
        graph = _make_graph()

        ctx = assembler.assemble_file_page(
            parsed, graph, {}, {}, {}, b"x = 1",
            kg_context=None,
        )
        assert ctx.kg_layer_name == ""

    def test_kg_entry_point_role(self):
        assembler = ContextAssembler(_make_config())
        parsed = _make_parsed_file()
        graph = _make_graph()
        kg = _make_kg_file_context(role="entry_point")

        ctx = assembler.assemble_file_page(
            parsed, graph, {}, {}, {}, b"x = 1",
            kg_context=kg,
        )
        assert ctx.kg_layer_role == "entry_point"

    def test_kg_internal_role(self):
        assembler = ContextAssembler(_make_config())
        parsed = _make_parsed_file()
        graph = _make_graph()
        kg = _make_kg_file_context(role="internal")

        ctx = assembler.assemble_file_page(
            parsed, graph, {}, {}, {}, b"x = 1",
            kg_context=kg,
        )
        assert ctx.kg_layer_role == "internal"

    def test_kg_no_tour_step(self):
        assembler = ContextAssembler(_make_config())
        parsed = _make_parsed_file()
        graph = _make_graph()
        kg = _make_kg_file_context(tour_step=None)

        ctx = assembler.assemble_file_page(
            parsed, graph, {}, {}, {}, b"x = 1",
            kg_context=kg,
        )
        assert ctx.kg_tour_step is None
        assert ctx.kg_layer_name == "Core Pipeline"

    def test_kg_empty_neighbors(self):
        assembler = ContextAssembler(_make_config())
        parsed = _make_parsed_file()
        graph = _make_graph()
        kg = _make_kg_file_context(neighbors=[])

        ctx = assembler.assemble_file_page(
            parsed, graph, {}, {}, {}, b"x = 1",
            kg_context=kg,
        )
        assert ctx.kg_neighbors == []


class TestTokenBudgetWithKG:
    def test_kg_reserves_budget(self):
        assembler = ContextAssembler(_make_config(token_budget=4000))
        parsed = _make_parsed_file()
        graph = _make_graph()
        kg = _make_kg_file_context()

        ctx_with = assembler.assemble_file_page(
            parsed, graph, {}, {}, {}, b"x = 1",
            kg_context=kg,
        )
        ctx_without = assembler.assemble_file_page(
            parsed, graph, {}, {}, {}, b"x = 1",
        )
        assert ctx_with.estimated_tokens >= ctx_without.estimated_tokens

    def test_kg_budget_does_not_exceed_800(self):
        assembler = ContextAssembler(_make_config(token_budget=4000))
        kg = _make_kg_file_context(
            layer_description="A" * 2000,
            node_summary="B" * 2000,
        )
        cost = assembler._estimate_kg_tokens(kg)
        assert cost <= 800


# ---------------------------------------------------------------------------
# Template rendering tests
# ---------------------------------------------------------------------------


class TestFilePageTemplate:
    @pytest.fixture
    def jinja_env(self):
        from jinja2 import Environment, FileSystemLoader
        from pathlib import Path

        template_dir = Path(__file__).resolve().parents[3] / "packages" / "core" / "src" / "repowise" / "core" / "generation" / "templates"
        return Environment(loader=FileSystemLoader(str(template_dir)))

    def test_tier1_template_renders_kg_layer(self, jinja_env):
        tmpl = jinja_env.get_template("file_page.j2")
        ctx = FilePageContext(
            file_path="src/core.py",
            language="python",
            docstring=None,
            symbols=[],
            imports=[],
            exports=[],
            file_source_snippet="",
            pagerank_score=0.0,
            betweenness_score=0.0,
            community_id=0,
            dependents=[],
            dependencies=[],
            is_api_contract=False,
            is_entry_point=False,
            is_test=False,
            parse_errors=[],
            estimated_tokens=0,
            kg_layer_name="Core Pipeline",
            kg_layer_description="Central processing logic",
            kg_layer_role="edge_connector",
            kg_neighbors=[{"path": "src/utils.py", "relationship": "imports", "same_layer": True}],
            kg_tour_step={"order": 1, "title": "Start Here", "description": "Begin exploration."},
            kg_node_summary="Core module",
        )
        rendered = tmpl.render(ctx=ctx)
        assert "## Architectural Layer" in rendered
        assert "**Core Pipeline**" in rendered
        assert "**edge connector**" in rendered
        assert "## Architectural Neighbors" in rendered
        assert "`src/utils.py`" in rendered
        assert "same layer" in rendered
        assert "## Codebase Tour" in rendered
        assert "Step 1: Start Here" in rendered

    def test_tier1_template_entry_point_role(self, jinja_env):
        tmpl = jinja_env.get_template("file_page.j2")
        ctx = FilePageContext(
            file_path="src/main.py",
            language="python",
            docstring=None,
            symbols=[],
            imports=[],
            exports=[],
            file_source_snippet="",
            pagerank_score=0.0,
            betweenness_score=0.0,
            community_id=0,
            dependents=[],
            dependencies=[],
            is_api_contract=False,
            is_entry_point=True,
            is_test=False,
            parse_errors=[],
            estimated_tokens=0,
            kg_layer_name="CLI",
            kg_layer_description="Command line interface",
            kg_layer_role="entry_point",
        )
        rendered = tmpl.render(ctx=ctx)
        assert "**layer entry point**" in rendered
        assert "edge connector" not in rendered

    def test_tier1_template_no_kg(self, jinja_env):
        tmpl = jinja_env.get_template("file_page.j2")
        ctx = FilePageContext(
            file_path="src/core.py",
            language="python",
            docstring=None,
            symbols=[],
            imports=[],
            exports=[],
            file_source_snippet="",
            pagerank_score=0.0,
            betweenness_score=0.0,
            community_id=0,
            dependents=[],
            dependencies=[],
            is_api_contract=False,
            is_entry_point=False,
            is_test=False,
            parse_errors=[],
            estimated_tokens=0,
        )
        rendered = tmpl.render(ctx=ctx)
        assert "## Architectural Layer" not in rendered
        assert "## Architectural Neighbors" not in rendered
        assert "## Codebase Tour" not in rendered

    def test_tier1_template_kg_signals(self, jinja_env):
        tmpl = jinja_env.get_template("file_page.j2")
        ctx = FilePageContext(
            file_path="src/core.py",
            language="python",
            docstring=None,
            symbols=[],
            imports=[],
            exports=[],
            file_source_snippet="",
            pagerank_score=0.0,
            betweenness_score=0.0,
            community_id=0,
            dependents=[],
            dependencies=[],
            is_api_contract=False,
            is_entry_point=False,
            is_test=False,
            parse_errors=[],
            estimated_tokens=0,
            kg_layer_name="Core Pipeline",
            kg_layer_role="edge_connector",
        )
        rendered = tmpl.render(ctx=ctx)
        assert "part of the Core Pipeline layer" in rendered
        assert "edge connector (imported cross-layer)" in rendered

    def test_tier2_template_renders_kg_layer(self, jinja_env):
        tmpl = jinja_env.get_template("file_page_tier2.j2")
        ctx = FilePageContext(
            file_path="src/utils.py",
            language="python",
            docstring=None,
            symbols=[],
            imports=[],
            exports=[],
            file_source_snippet="",
            pagerank_score=0.0,
            betweenness_score=0.0,
            community_id=0,
            dependents=[],
            dependencies=[],
            is_api_contract=False,
            is_entry_point=False,
            is_test=False,
            parse_errors=[],
            estimated_tokens=0,
            kg_layer_name="Core Pipeline",
            kg_layer_role="internal",
        )
        rendered = tmpl.render(ctx=ctx)
        assert "**Layer:** Core Pipeline" in rendered
        assert "**Role:** internal" in rendered

    def test_tier2_template_no_kg(self, jinja_env):
        tmpl = jinja_env.get_template("file_page_tier2.j2")
        ctx = FilePageContext(
            file_path="src/utils.py",
            language="python",
            docstring=None,
            symbols=[],
            imports=[],
            exports=[],
            file_source_snippet="",
            pagerank_score=0.0,
            betweenness_score=0.0,
            community_id=0,
            dependents=[],
            dependencies=[],
            is_api_contract=False,
            is_entry_point=False,
            is_test=False,
            parse_errors=[],
            estimated_tokens=0,
        )
        rendered = tmpl.render(ctx=ctx)
        assert "**Layer:**" not in rendered

    def test_tier1_cross_layer_neighbor(self, jinja_env):
        tmpl = jinja_env.get_template("file_page.j2")
        ctx = FilePageContext(
            file_path="src/core.py",
            language="python",
            docstring=None,
            symbols=[],
            imports=[],
            exports=[],
            file_source_snippet="",
            pagerank_score=0.0,
            betweenness_score=0.0,
            community_id=0,
            dependents=[],
            dependencies=[],
            is_api_contract=False,
            is_entry_point=False,
            is_test=False,
            parse_errors=[],
            estimated_tokens=0,
            kg_layer_name="Core",
            kg_layer_description="Core logic",
            kg_layer_role="internal",
            kg_neighbors=[
                {"path": "src/api.py", "relationship": "imported_by", "same_layer": False},
            ],
        )
        rendered = tmpl.render(ctx=ctx)
        assert "cross-layer" in rendered
