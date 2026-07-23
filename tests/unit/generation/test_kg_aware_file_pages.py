"""Tests for KG-enriched file page context assembly and template rendering."""

from __future__ import annotations

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
            {
                "path": "src/utils.py",
                "name": "utils.py",
                "same_layer": True,
                "relationship": "imports",
            },
            {
                "path": "src/api.py",
                "name": "api.py",
                "same_layer": False,
                "relationship": "imported_by",
            },
        ],
        tour_step={
            "order": 2,
            "title": "Core Logic",
            "description": "Explore the core processing pipeline.",
        },
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
            parsed,
            graph,
            {"src/core.py": 0.5},
            {},
            {},
            b"x = 1",
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
            parsed,
            graph,
            {},
            {},
            {},
            b"x = 1",
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
            parsed,
            graph,
            {},
            {},
            {},
            b"x = 1",
            kg_context=None,
        )
        assert ctx.kg_layer_name == ""

    def test_kg_entry_point_role(self):
        assembler = ContextAssembler(_make_config())
        parsed = _make_parsed_file()
        graph = _make_graph()
        kg = _make_kg_file_context(role="entry_point")

        ctx = assembler.assemble_file_page(
            parsed,
            graph,
            {},
            {},
            {},
            b"x = 1",
            kg_context=kg,
        )
        assert ctx.kg_layer_role == "entry_point"

    def test_kg_internal_role(self):
        assembler = ContextAssembler(_make_config())
        parsed = _make_parsed_file()
        graph = _make_graph()
        kg = _make_kg_file_context(role="internal")

        ctx = assembler.assemble_file_page(
            parsed,
            graph,
            {},
            {},
            {},
            b"x = 1",
            kg_context=kg,
        )
        assert ctx.kg_layer_role == "internal"

    def test_kg_no_tour_step(self):
        assembler = ContextAssembler(_make_config())
        parsed = _make_parsed_file()
        graph = _make_graph()
        kg = _make_kg_file_context(tour_step=None)

        ctx = assembler.assemble_file_page(
            parsed,
            graph,
            {},
            {},
            {},
            b"x = 1",
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
            parsed,
            graph,
            {},
            {},
            {},
            b"x = 1",
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
            parsed,
            graph,
            {},
            {},
            {},
            b"x = 1",
            kg_context=kg,
        )
        ctx_without = assembler.assemble_file_page(
            parsed,
            graph,
            {},
            {},
            {},
            b"x = 1",
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
        from pathlib import Path

        from jinja2 import Environment, FileSystemLoader

        from repowise.core.generation.page_generator.structural import (
            as_markdown,
            oneline,
            signature,
        )

        template_dir = (
            Path(__file__).resolve().parents[3]
            / "packages"
            / "core"
            / "src"
            / "repowise"
            / "core"
            / "generation"
            / "templates"
        )
        env = Environment(loader=FileSystemLoader(str(template_dir)))
        # Mirror the production environment, which registers these in
        # PageGenerator.__init__. Without them the templates fail to compile.
        env.filters["oneline"] = oneline
        env.filters["as_markdown"] = as_markdown
        env.filters["signature"] = signature
        return env

    def test_file_page_renders_kg_layer(self, jinja_env):
        tmpl = jinja_env.get_template("file_page.j2")
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

    def test_file_page_without_kg(self, jinja_env):
        tmpl = jinja_env.get_template("file_page.j2")
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
