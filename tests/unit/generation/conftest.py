"""Shared fixtures for generation unit tests."""

from __future__ import annotations

from datetime import datetime, timezone

import networkx as nx
import pytest

from repowise.core.ingestion.models import (
    FileInfo,
    Import,
    PackageInfo,
    ParsedFile,
    RepoStructure,
    Symbol,
)
from repowise.core.providers.llm.mock import MockProvider
from repowise.core.generation.models import GenerationConfig


# ---------------------------------------------------------------------------
# Helper functions (module-level, not fixtures)
# ---------------------------------------------------------------------------


def _make_file_info(
    path: str = "python_pkg/calculator.py",
    language: str = "python",
    is_api_contract: bool = False,
    is_entry_point: bool = False,
    is_test: bool = False,
    is_config: bool = False,
    size_bytes: int = 512,
) -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language=language,
        size_bytes=size_bytes,
        git_hash="abc123",
        last_modified=datetime(2026, 1, 1, tzinfo=timezone.utc),
        is_test=is_test,
        is_config=is_config,
        is_api_contract=is_api_contract,
        is_entry_point=is_entry_point,
    )


def _make_symbol(
    name: str = "add",
    kind: str = "function",
    file_path: str = "python_pkg/calculator.py",
    signature: str = "def add(a: int, b: int) -> int:",
    visibility: str = "public",
    docstring: str | None = "Add two numbers.",
    is_async: bool = False,
    complexity_estimate: int = 1,
    parent_name: str | None = None,
) -> Symbol:
    return Symbol(
        id=f"{file_path}::{name}",
        name=name,
        qualified_name=f"python_pkg.calculator.{name}",
        kind=kind,
        signature=signature,
        start_line=1,
        end_line=10,
        docstring=docstring,
        decorators=[],
        visibility=visibility,
        is_async=is_async,
        complexity_estimate=complexity_estimate,
        language="python",
        parent_name=parent_name,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sample_config() -> GenerationConfig:
    return GenerationConfig(
        max_tokens=1024,
        token_budget=2000,
        max_concurrency=2,
    )


@pytest.fixture(scope="module")
def mock_provider() -> MockProvider:
    return MockProvider()


@pytest.fixture(scope="module")
def sample_parsed_file() -> ParsedFile:
    """A ParsedFile with 3 symbols, 2 imports, and a module docstring."""
    fi = _make_file_info()
    calc_class = _make_symbol(
        name="Calculator",
        kind="class",
        signature="class Calculator:",
        docstring="A simple calculator.",
    )
    add_method = _make_symbol(
        name="add",
        kind="method",
        signature="def add(self, a: int, b: int) -> int:",
        parent_name="Calculator",
    )
    internal = _make_symbol(
        name="_internal",
        kind="function",
        signature="def _internal() -> None:",
        visibility="private",
        docstring=None,
    )
    imports = [
        Import(
            raw_statement="from python_pkg import models",
            module_path="python_pkg.models",
            imported_names=["models"],
            is_relative=False,
            resolved_file="python_pkg/models.py",
        ),
        Import(
            raw_statement="import utils",
            module_path="utils",
            imported_names=["utils"],
            is_relative=False,
            resolved_file="python_pkg/utils.py",
        ),
    ]
    return ParsedFile(
        file_info=fi,
        symbols=[calc_class, add_method, internal],
        imports=imports,
        exports=["Calculator", "add"],
        docstring="Calculator module providing arithmetic operations.",
        parse_errors=[],
        content_hash="abc123",
    )


@pytest.fixture(scope="module")
def sample_graph(sample_parsed_file: ParsedFile) -> nx.DiGraph:
    """3-node DiGraph: calculator → models, calculator → utils."""
    g = nx.DiGraph()
    calc = sample_parsed_file.file_info.path
    models = "python_pkg/models.py"
    utils = "python_pkg/utils.py"
    g.add_node(calc, language="python", symbol_count=3, has_error=False)
    g.add_node(models, language="python", symbol_count=2, has_error=False)
    g.add_node(utils, language="python", symbol_count=1, has_error=False)
    g.add_edge(calc, models, imported_names=["models"])
    g.add_edge(calc, utils, imported_names=["utils"])
    return g


@pytest.fixture(scope="module")
def graph_metrics(sample_graph: nx.DiGraph, sample_parsed_file: ParsedFile) -> dict:
    """PageRank, betweenness centrality, and community for sample_graph nodes."""
    import networkx as nx

    g = sample_graph
    pagerank = nx.pagerank(g)
    betweenness = nx.betweenness_centrality(g)
    # Simple community: all in community 0
    community = {node: 0 for node in g.nodes()}
    return {
        "pagerank": pagerank,
        "betweenness": betweenness,
        "community": community,
    }


@pytest.fixture(scope="module")
def sample_source_bytes() -> bytes:
    return b"""\
class Calculator:
    \"\"\"A simple calculator.\"\"\"

    def add(self, a: int, b: int) -> int:
        return a + b

    def _internal(self) -> None:
        pass
"""


@pytest.fixture(scope="module")
def sample_repo_structure() -> RepoStructure:
    pkg = PackageInfo(
        name="python_pkg",
        path="python_pkg",
        language="python",
        entry_points=["python_pkg/calculator.py"],
        manifest_file="pyproject.toml",
    )
    return RepoStructure(
        is_monorepo=False,
        packages=[pkg],
        root_language_distribution={"python": 1.0},
        total_files=3,
        total_loc=100,
        entry_points=["python_pkg/calculator.py"],
    )
