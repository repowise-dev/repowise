"""Characterization tests for the ``repowise.server.schemas`` package.

These guard the split of the former single ``schemas.py`` module into a
package: the public import surface must be unchanged, and the deduplicated
graph-node models must serialize identically to their pre-split form
(same field set, order, and JSON output). Submodules load on first access,
so the facade must not import them all.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from repowise.server import schemas


def test_facade_reexports_every_model() -> None:
    """Every name in ``__all__`` resolves to a class on the package."""
    assert schemas.__all__, "__all__ must not be empty"
    for name in schemas.__all__:
        assert hasattr(schemas, name), f"{name} missing from schemas facade"


# The three graph-node response models historically duplicated the same 16
# fields verbatim; the split collapses that via inheritance. Field order is
# part of the serialized contract, so pin it explicitly.
_BASE_NODE_FIELDS = [
    "node_id",
    "node_type",
    "language",
    "symbol_count",
    "pagerank",
    "betweenness",
    "community_id",
    "is_test",
    "is_entry_point",
    "has_doc",
    "is_hotspot",
    "churn_percentile",
    "is_dead",
    "dead_confidence",
    "has_decision",
    "primary_owner",
]


def test_graph_node_field_order_preserved() -> None:
    assert list(schemas.GraphNodeResponse.model_fields) == _BASE_NODE_FIELDS
    assert list(schemas.DeadCodeGraphNodeResponse.model_fields) == [
        *_BASE_NODE_FIELDS,
        "confidence_group",
    ]
    assert list(schemas.HotFilesNodeResponse.model_fields) == [
        *_BASE_NODE_FIELDS,
        "commit_count",
    ]


def test_graph_node_serialization_unchanged() -> None:
    base_kwargs = dict(
        node_id="pkg/mod.py",
        node_type="file",
        language="python",
        symbol_count=3,
        pagerank=0.5,
        betweenness=0.1,
        community_id=2,
    )
    expected_base = {
        "node_id": "pkg/mod.py",
        "node_type": "file",
        "language": "python",
        "symbol_count": 3,
        "pagerank": 0.5,
        "betweenness": 0.1,
        "community_id": 2,
        "is_test": False,
        "is_entry_point": False,
        "has_doc": False,
        "is_hotspot": False,
        "churn_percentile": None,
        "is_dead": False,
        "dead_confidence": None,
        "has_decision": False,
        "primary_owner": None,
    }
    assert schemas.GraphNodeResponse(**base_kwargs).model_dump() == expected_base
    assert schemas.DeadCodeGraphNodeResponse(
        **base_kwargs, confidence_group="certain"
    ).model_dump() == {**expected_base, "confidence_group": "certain"}
    assert schemas.HotFilesNodeResponse(**base_kwargs, commit_count=7).model_dump() == {
        **expected_base,
        "commit_count": 7,
    }


def test_lazy_map_matches_the_static_imports_and_all() -> None:
    """The runtime map, the type-checking imports and ``__all__`` list one set."""
    tree = ast.parse(Path(schemas.__file__).read_text(encoding="utf-8"))
    guarded = next(
        n
        for n in tree.body
        if isinstance(n, ast.If) and isinstance(n.test, ast.Name) and n.test.id == "TYPE_CHECKING"
    )
    static = {
        alias.name: node.module
        for node in guarded.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert static == schemas._SUBMODULE_OF
    assert sum(map(len, schemas._EXPORTS.values())) == len(static)
    assert sorted(schemas.__all__) == sorted(static)


@pytest.mark.parametrize(
    ("statement", "loaded"),
    [
        ("from repowise.server.schemas import ZoomMapResponse", "zoom"),
        ("from repowise.server.schemas.risk_semantics import RiskScalarSemantics", "risk_semantics"),
    ],
)
def test_one_model_does_not_load_every_submodule(statement: str, loaded: str) -> None:
    probe = (
        f"import sys; {statement}; "
        "print(sorted(m for m in sys.modules if m.startswith('repowise.server.schemas.')))"
    )
    # Same sys.path as this process, so the child imports this checkout.
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    out = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        timeout=60,
    ).stdout
    assert out.strip() == f"['repowise.server.schemas.{loaded}']"
