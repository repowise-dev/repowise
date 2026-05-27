"""Characterization tests for the ``repowise.server.schemas`` package.

These guard the split of the former single ``schemas.py`` module into a
package: the public import surface must be unchanged, and the deduplicated
graph-node models must serialize identically to their pre-split form
(same field set, order, and JSON output).
"""

from __future__ import annotations

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
