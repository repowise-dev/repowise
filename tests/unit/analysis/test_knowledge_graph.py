"""Tests for the deterministic knowledge graph skeleton builder."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from repowise.core.analysis.knowledge_graph import (
    KnowledgeGraphResult,
    _classify_complexity,
    _classify_file_type,
    _slugify,
    build_knowledge_graph_skeleton,
    compute_kg_fingerprint,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class FakeFileInfo:
    path: str
    abs_path: str = ""
    language: str = "python"
    size_bytes: int = 1000
    git_hash: str = ""
    last_modified: Any = None
    is_test: bool = False
    is_config: bool = False
    is_api_contract: bool = False
    is_entry_point: bool = False
    line_count: int = 100


@dataclass
class FakeSymbol:
    id: str = ""
    name: str = "my_func"
    qualified_name: str = "module.my_func"
    kind: str = "function"
    signature: str = "def my_func():"
    start_line: int = 1
    end_line: int = 10
    docstring: str | None = None


@dataclass
class FakeParsedFile:
    file_info: FakeFileInfo = field(default_factory=lambda: FakeFileInfo(path="src/main.py"))
    symbols: list = field(default_factory=list)
    imports: list = field(default_factory=list)
    exports: list = field(default_factory=list)


def _make_graph_builder(
    nodes: dict[str, dict] | None = None,
    edges: list[tuple[str, str, dict]] | None = None,
    communities: dict[str, int] | None = None,
    community_infos: dict[int, Any] | None = None,
    pagerank: dict[str, float] | None = None,
):
    """Create a mock GraphBuilder with configurable graph data."""
    import networkx as nx

    g = nx.DiGraph()
    if nodes:
        for nid, data in nodes.items():
            g.add_node(nid, **data)
    if edges:
        for u, v, data in edges:
            g.add_edge(u, v, **data)

    builder = MagicMock()
    builder.graph.return_value = g
    builder.pagerank.return_value = pagerank or {}
    builder.betweenness_centrality.return_value = {}
    builder.community_detection.return_value = communities or {}
    builder.community_info.return_value = community_infos or {}
    return builder


def _make_community_info(cid: int, label: str, members: list[str]):
    return SimpleNamespace(
        community_id=cid,
        label=label,
        members=members,
        size=len(members),
        cohesion=0.8,
        dominant_language="python",
    )


@pytest.fixture
def simple_parsed_files():
    return [
        FakeParsedFile(
            file_info=FakeFileInfo(path="src/main.py", is_entry_point=True),
            symbols=[FakeSymbol(name="main", kind="function")],
        ),
        FakeParsedFile(
            file_info=FakeFileInfo(path="src/core.py"),
            symbols=[
                FakeSymbol(name="Core", kind="class"),
                FakeSymbol(name="process", kind="method"),
            ],
        ),
        FakeParsedFile(
            file_info=FakeFileInfo(path="tests/test_main.py", is_test=True),
            symbols=[FakeSymbol(name="test_main", kind="function")],
        ),
    ]


@pytest.fixture
def simple_graph_builder():
    nodes = {
        "src/main.py": {"node_type": "file", "language": "python", "is_test": False, "is_entry_point": True},
        "src/core.py": {"node_type": "file", "language": "python", "is_test": False},
        "tests/test_main.py": {"node_type": "file", "language": "python", "is_test": True},
    }
    edges = [
        ("src/main.py", "src/core.py", {"edge_type": "imports", "confidence": 1.0}),
        ("tests/test_main.py", "src/main.py", {"edge_type": "imports", "confidence": 1.0}),
    ]
    communities = {"src/main.py": 0, "src/core.py": 0, "tests/test_main.py": 1}
    community_infos = {
        0: _make_community_info(0, "src/core", ["src/main.py", "src/core.py"]),
        1: _make_community_info(1, "tests", ["tests/test_main.py"]),
    }
    pagerank = {"src/main.py": 0.5, "src/core.py": 0.3, "tests/test_main.py": 0.2}
    return _make_graph_builder(nodes, edges, communities, community_infos, pagerank)


@pytest.fixture
def repo_structure():
    return SimpleNamespace(
        is_monorepo=False,
        total_files=3,
        total_loc=300,
        entry_points=["src/main.py"],
        packages=[],
        root_language_distribution={"python": 1.0},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSkeletonBuilder:
    def test_builds_file_nodes(self, simple_parsed_files, simple_graph_builder, repo_structure):
        result = build_knowledge_graph_skeleton(
            parsed_files=simple_parsed_files,
            graph_builder=simple_graph_builder,
            repo_structure=repo_structure,
            tech_stack=[],
            external_systems=[],
        )
        file_nodes = [n for n in result.nodes if n["type"] == "file"]
        assert len(file_nodes) == 3  # main.py, core.py, test_main.py
        assert all(n["id"].startswith("file:") for n in file_nodes)

    def test_builds_all_node_types(self, simple_parsed_files, simple_graph_builder, repo_structure):
        result = build_knowledge_graph_skeleton(
            parsed_files=simple_parsed_files,
            graph_builder=simple_graph_builder,
            repo_structure=repo_structure,
            tech_stack=[],
            external_systems=[],
        )
        node_ids = {n["id"] for n in result.nodes}
        assert "file:src/main.py" in node_ids
        assert "file:src/core.py" in node_ids
        assert "file:tests/test_main.py" in node_ids

    def test_test_file_tagged(self, simple_parsed_files, simple_graph_builder, repo_structure):
        result = build_knowledge_graph_skeleton(
            parsed_files=simple_parsed_files,
            graph_builder=simple_graph_builder,
            repo_structure=repo_structure,
            tech_stack=[],
            external_systems=[],
        )
        test_node = next(n for n in result.nodes if n["id"] == "file:tests/test_main.py")
        assert "test" in test_node["tags"]

    def test_entry_point_tagged(self, simple_parsed_files, simple_graph_builder, repo_structure):
        result = build_knowledge_graph_skeleton(
            parsed_files=simple_parsed_files,
            graph_builder=simple_graph_builder,
            repo_structure=repo_structure,
            tech_stack=[],
            external_systems=[],
        )
        main_node = next(n for n in result.nodes if n["id"] == "file:src/main.py")
        assert "entry_point" in main_node["tags"]

    def test_builds_import_edges(self, simple_parsed_files, simple_graph_builder, repo_structure):
        result = build_knowledge_graph_skeleton(
            parsed_files=simple_parsed_files,
            graph_builder=simple_graph_builder,
            repo_structure=repo_structure,
            tech_stack=[],
            external_systems=[],
        )
        import_edges = [e for e in result.edges if e["type"] == "imports"]
        assert len(import_edges) >= 1
        assert all(e["direction"] == "forward" for e in import_edges)

    def test_tested_by_edges(self, simple_parsed_files, simple_graph_builder, repo_structure):
        result = build_knowledge_graph_skeleton(
            parsed_files=simple_parsed_files,
            graph_builder=simple_graph_builder,
            repo_structure=repo_structure,
            tech_stack=[],
            external_systems=[],
        )
        tested_by = [e for e in result.edges if e["type"] == "tested_by"]
        assert len(tested_by) == 1
        assert tested_by[0]["source"] == "file:tests/test_main.py"
        assert tested_by[0]["target"] == "file:src/main.py"

    def test_builds_layers_from_communities(self, simple_parsed_files, simple_graph_builder, repo_structure):
        result = build_knowledge_graph_skeleton(
            parsed_files=simple_parsed_files,
            graph_builder=simple_graph_builder,
            repo_structure=repo_structure,
            tech_stack=[],
            external_systems=[],
        )
        assert len(result.layers) == 2
        assert all(layer["id"].startswith("layer:") for layer in result.layers)
        layer_names = {l["name"] for l in result.layers}
        assert "src/core" in layer_names
        assert "tests" in layer_names

    def test_layers_contain_node_ids(self, simple_parsed_files, simple_graph_builder, repo_structure):
        result = build_knowledge_graph_skeleton(
            parsed_files=simple_parsed_files,
            graph_builder=simple_graph_builder,
            repo_structure=repo_structure,
            tech_stack=[],
            external_systems=[],
        )
        core_layer = next(l for l in result.layers if l["name"] == "src/core")
        assert "file:src/main.py" in core_layer["nodeIds"]
        assert "file:src/core.py" in core_layer["nodeIds"]

    def test_no_communities_gives_empty_layers(self, simple_parsed_files, repo_structure):
        builder = _make_graph_builder(
            nodes={"src/main.py": {"node_type": "file"}},
            communities={},
            community_infos={},
            pagerank={"src/main.py": 0.5},
        )
        result = build_knowledge_graph_skeleton(
            parsed_files=simple_parsed_files,
            graph_builder=builder,
            repo_structure=repo_structure,
            tech_stack=[],
            external_systems=[],
        )
        assert result.layers == []

    def test_project_metadata(self, simple_parsed_files, simple_graph_builder, repo_structure):
        from pathlib import Path

        result = build_knowledge_graph_skeleton(
            parsed_files=simple_parsed_files,
            graph_builder=simple_graph_builder,
            repo_structure=repo_structure,
            tech_stack=[{"name": "FastAPI", "version": "0.100", "category": "framework"}],
            external_systems=[],
            repo_path=Path("/tmp/my-repo"),
        )
        assert result.project["name"] == "my-repo"
        assert result.project["is_monorepo"] is False
        assert len(result.project["tech_stack"]) == 1


class TestComplexityClassification:
    def test_simple(self):
        assert _classify_complexity(2, 50) == "simple"

    def test_moderate(self):
        assert _classify_complexity(8, 200) == "moderate"

    def test_complex_by_symbols(self):
        assert _classify_complexity(20, 200) == "complex"

    def test_complex_by_lines(self):
        assert _classify_complexity(5, 600) == "complex"

    def test_edge_simple(self):
        assert _classify_complexity(3, 99) == "simple"

    def test_edge_complex(self):
        assert _classify_complexity(16, 100) == "complex"


class TestFileTypeClassification:
    def test_python_file(self):
        assert _classify_file_type("src/main.py", "python", False) == "file"

    def test_config_by_extension(self):
        assert _classify_file_type("config.yaml", "yaml", False) == "config"

    def test_config_by_flag(self):
        assert _classify_file_type("settings.py", "python", True) == "config"

    def test_dockerfile(self):
        assert _classify_file_type("Dockerfile", "", False) == "service"

    def test_markdown(self):
        assert _classify_file_type("README.md", "", False) == "document"

    def test_makefile(self):
        assert _classify_file_type("Makefile", "", False) == "service"


class TestSlugify:
    def test_normal(self):
        assert _slugify("Core Ingestion") == "core-ingestion"

    def test_special_chars(self):
        assert _slugify("src/core") == "src-core"

    def test_empty(self):
        assert _slugify("") == "unknown"


class TestFingerprint:
    def test_stability(self, simple_graph_builder):
        fp1 = compute_kg_fingerprint(simple_graph_builder)
        fp2 = compute_kg_fingerprint(simple_graph_builder)
        assert fp1 == fp2

    def test_changes_on_graph_change(self):
        builder1 = _make_graph_builder(
            nodes={"a": {"node_type": "file"}},
            communities={"a": 0},
            pagerank={"a": 0.5},
        )
        builder2 = _make_graph_builder(
            nodes={"a": {"node_type": "file"}, "b": {"node_type": "file"}},
            edges=[("a", "b", {"edge_type": "imports"})],
            communities={"a": 0, "b": 0},
            pagerank={"a": 0.5, "b": 0.3},
        )
        fp1 = compute_kg_fingerprint(builder1)
        fp2 = compute_kg_fingerprint(builder2)
        assert fp1 != fp2

    def test_length(self, simple_graph_builder):
        fp = compute_kg_fingerprint(simple_graph_builder)
        assert len(fp) == 16


class TestToDict:
    def test_schema(self, simple_parsed_files, simple_graph_builder, repo_structure):
        result = build_knowledge_graph_skeleton(
            parsed_files=simple_parsed_files,
            graph_builder=simple_graph_builder,
            repo_structure=repo_structure,
            tech_stack=[],
            external_systems=[],
        )
        d = result.to_dict()
        assert d["version"] == "1.0.0"
        assert isinstance(d["nodes"], list)
        assert isinstance(d["edges"], list)
        assert isinstance(d["layers"], list)
        assert isinstance(d["tour"], list)
        assert isinstance(d["project"], dict)

    def test_roundtrip_json(self, simple_parsed_files, simple_graph_builder, repo_structure):
        import json

        result = build_knowledge_graph_skeleton(
            parsed_files=simple_parsed_files,
            graph_builder=simple_graph_builder,
            repo_structure=repo_structure,
            tech_stack=[],
            external_systems=[],
        )
        serialized = json.dumps(result.to_dict())
        deserialized = json.loads(serialized)
        assert deserialized["version"] == "1.0.0"
        assert len(deserialized["nodes"]) == len(result.nodes)
