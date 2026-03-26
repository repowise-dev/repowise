"""Unit tests for repowise.cli.cost_estimator."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest

from repowise.cli.cost_estimator import (
    CostEstimate,
    PageTypePlan,
    build_generation_plan,
    estimate_cost,
)


# ---------------------------------------------------------------------------
# Fixtures — lightweight fakes to avoid importing full ingestion models
# ---------------------------------------------------------------------------


@dataclass
class FakeFileInfo:
    path: str = "src/main.py"
    abs_path: str = "/repo/src/main.py"
    language: str = "python"
    is_test: bool = False
    is_api_contract: bool = False
    is_entry_point: bool = False
    size_bytes: int = 1000


@dataclass
class FakeSymbol:
    name: str = "my_func"
    visibility: str = "public"
    kind: str = "function"
    start_line: int = 1
    end_line: int = 10
    qualified_name: str = "main.my_func"
    signature: str = "def my_func():"


@dataclass
class FakeParsedFile:
    file_info: FakeFileInfo = field(default_factory=FakeFileInfo)
    symbols: list = field(default_factory=list)


@dataclass
class FakeConfig:
    top_symbol_percentile: float = 0.10
    file_page_top_percentile: float = 0.20
    file_page_min_symbols: int = 1
    max_pages_pct: float = 0.10


def _make_graph_builder(files: list[FakeParsedFile]):
    """Create a mock GraphBuilder with the necessary methods."""
    import networkx as nx

    graph = nx.DiGraph()
    for f in files:
        graph.add_node(f.file_info.path, symbol_count=len(f.symbols), language=f.file_info.language)

    builder = MagicMock()
    builder.graph.return_value = graph
    builder.pagerank.return_value = {f.file_info.path: 1.0 / max(len(files), 1) for f in files}
    builder.betweenness_centrality.return_value = {f.file_info.path: 0.0 for f in files}
    builder.strongly_connected_components.return_value = []
    return builder


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildGenerationPlan:
    def test_empty_repo(self):
        builder = _make_graph_builder([])
        plans = build_generation_plan([], builder, FakeConfig())
        # Still get repo_overview + architecture_diagram
        assert any(p.page_type == "repo_overview" for p in plans)
        assert any(p.page_type == "architecture_diagram" for p in plans)

    def test_single_python_file_with_symbol(self):
        fi = FakeFileInfo(path="src/app.py", language="python")
        sym = FakeSymbol(name="handler", visibility="public")
        pf = FakeParsedFile(file_info=fi, symbols=[sym])
        builder = _make_graph_builder([pf])

        plans = build_generation_plan([pf], builder, FakeConfig())
        types = {p.page_type for p in plans}

        assert "symbol_spotlight" in types
        assert "file_page" in types
        assert "repo_overview" in types

    def test_api_contract_file(self):
        fi = FakeFileInfo(path="api/openapi.yaml", language="openapi", is_api_contract=True)
        pf = FakeParsedFile(file_info=fi, symbols=[])
        builder = _make_graph_builder([pf])

        plans = build_generation_plan([pf], builder, FakeConfig())
        assert any(p.page_type == "api_contract" and p.count == 1 for p in plans)

    def test_infra_file(self):
        fi = FakeFileInfo(path="Dockerfile", language="dockerfile")
        pf = FakeParsedFile(file_info=fi, symbols=[])
        builder = _make_graph_builder([pf])

        plans = build_generation_plan([pf], builder, FakeConfig())
        assert any(p.page_type == "infra_page" and p.count == 1 for p in plans)

    def test_skip_infra(self):
        fi = FakeFileInfo(path="Dockerfile", language="dockerfile")
        pf = FakeParsedFile(file_info=fi, symbols=[])
        builder = _make_graph_builder([pf])

        plans = build_generation_plan([pf], builder, FakeConfig(), skip_infra=True)
        assert not any(p.page_type == "infra_page" for p in plans)

    def test_skip_tests(self):
        fi = FakeFileInfo(path="tests/test_foo.py", language="python", is_test=True)
        sym = FakeSymbol(name="test_bar", visibility="public")
        pf = FakeParsedFile(file_info=fi, symbols=[sym])
        builder = _make_graph_builder([pf])

        plans = build_generation_plan([pf], builder, FakeConfig(), skip_tests=True)
        assert not any(p.page_type == "symbol_spotlight" for p in plans)

    def test_scc_pages_counted(self):
        fi1 = FakeFileInfo(path="a.py", language="python")
        fi2 = FakeFileInfo(path="b.py", language="python")
        pf1 = FakeParsedFile(file_info=fi1, symbols=[FakeSymbol(name="a_func")])
        pf2 = FakeParsedFile(file_info=fi2, symbols=[FakeSymbol(name="b_func")])
        builder = _make_graph_builder([pf1, pf2])
        builder.strongly_connected_components.return_value = [frozenset({"a.py", "b.py"})]

        plans = build_generation_plan([pf1, pf2], builder, FakeConfig())
        assert any(p.page_type == "scc_page" and p.count == 1 for p in plans)


class TestEstimateCost:
    def test_mock_provider_zero_cost(self):
        plans = [PageTypePlan("file_page", 5, 2)]
        est = estimate_cost(plans, "mock", "mock-model")
        assert est.estimated_cost_usd == 0.0
        assert est.total_pages == 5

    def test_anthropic_has_cost(self):
        plans = [PageTypePlan("file_page", 10, 2)]
        est = estimate_cost(plans, "anthropic", "claude-sonnet-4-6")
        assert est.estimated_cost_usd > 0
        assert est.total_pages == 10
        assert est.estimated_input_tokens > 0
        assert est.estimated_output_tokens > 0

    def test_empty_plans(self):
        est = estimate_cost([], "anthropic", "claude-sonnet-4-6")
        assert est.total_pages == 0
        assert est.estimated_cost_usd == 0.0

    def test_multiple_page_types(self):
        plans = [
            PageTypePlan("api_contract", 2, 0),
            PageTypePlan("file_page", 5, 2),
            PageTypePlan("repo_overview", 1, 6),
        ]
        est = estimate_cost(plans, "openai", "gpt-4o")
        assert est.total_pages == 8
        assert est.estimated_cost_usd > 0
