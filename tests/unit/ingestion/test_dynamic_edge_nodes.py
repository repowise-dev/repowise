"""``add_dynamic_edges``: a target it has to create is classified like any file."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion.dynamic_hints.base import DynamicEdge
from repowise.core.ingestion.graph import GraphBuilder


@pytest.fixture
def builder(tmp_path: Path) -> GraphBuilder:
    gb = GraphBuilder(repo_path=tmp_path)
    gb._graph.add_node("src/app.py", node_type="file", language="python", is_test=False)
    return gb


def _edge(target: str) -> DynamicEdge:
    return DynamicEdge(
        source="src/app.py", target=target, edge_type="dynamic_imports", hint_source="test"
    )


def test_a_test_target_is_stored_as_test(builder: GraphBuilder) -> None:
    builder.add_dynamic_edges([_edge("tests/helpers/mod.py")])
    assert builder._graph.nodes["tests/helpers/mod.py"]["is_test"] is True


def test_a_production_target_is_stored_as_production(builder: GraphBuilder) -> None:
    builder.add_dynamic_edges([_edge("src/plugins/mod.py")])
    node = builder._graph.nodes["src/plugins/mod.py"]
    assert node["is_test"] is False
    assert node["language"] == "python"


def test_an_unknown_extension_leaves_language_unset(builder: GraphBuilder) -> None:
    builder.add_dynamic_edges([_edge("packages/ui/src")])
    assert "language" not in builder._graph.nodes["packages/ui/src"]
