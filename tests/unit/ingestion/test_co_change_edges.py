"""``add_co_change_edges``: the co-change records it reads are untrusted."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.ingestion.graph import GraphBuilder


@pytest.fixture
def builder(tmp_path: Path) -> GraphBuilder:
    """A real builder holding two file nodes and nothing else."""
    gb = GraphBuilder(repo_path=tmp_path)
    gb._graph.add_nodes_from(("a.py", "b.py"))
    return gb


def _meta(partners: object) -> dict:
    return {"co_change_partners_json": json.dumps(partners)}


def test_a_bare_string_partner_does_not_crash_the_pass(builder: GraphBuilder) -> None:
    b = builder
    meta = {"a.py": _meta(["b.py", {"file_path": "b.py", "co_change_count": 9}])}
    assert b.add_co_change_edges(meta) == 1
    edge = b._graph.edges["a.py", "b.py"]
    assert edge["edge_type"] == "co_changes"
    assert edge["weight"] == 9.0


def test_a_non_numeric_weight_does_not_crash_the_pass(builder: GraphBuilder) -> None:
    b = builder
    meta = {"a.py": _meta([{"file_path": "b.py", "co_change_count": "lots"}])}
    assert b.add_co_change_edges(meta) == 0


def test_the_pair_is_added_once_from_either_side(builder: GraphBuilder) -> None:
    b = builder
    meta = {
        "a.py": _meta([{"file_path": "b.py", "co_change_count": 9}]),
        "b.py": _meta([{"file_path": "a.py", "co_change_count": 9}]),
    }
    assert b.add_co_change_edges(meta) == 1
