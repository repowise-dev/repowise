"""Wire-shape tests for the coupling response models.

The graph assembly itself is covered by ``tests/unit/coupling/test_graph.py``;
here we lock the JSON keys the endpoint emits so the wire contract (mirrored in
``packages/types/src/coupling.ts``) cannot drift silently.
"""

from __future__ import annotations

from repowise.core.analysis.coupling import CouplingEdge, CouplingNode
from repowise.server.schemas import CouplingEdgeResponse, CouplingNodeResponse


def test_node_wire_shape() -> None:
    n = CouplingNode(file_path="a.py", module="api", score=3.4, nloc=420)
    assert CouplingNodeResponse(**vars(n)).model_dump() == {
        "file_path": "a.py",
        "module": "api",
        "score": 3.4,
        "nloc": 420,
    }


def test_node_null_score_module() -> None:
    n = CouplingNode(file_path="config.yaml", module=None, score=None, nloc=0)
    assert CouplingNodeResponse(**vars(n)).model_dump() == {
        "file_path": "config.yaml",
        "module": None,
        "score": None,
        "nloc": 0,
    }


def test_edge_wire_shape() -> None:
    e = CouplingEdge(
        source="a.py",
        target="b.py",
        strength=4.25,
        last_co_change="2026-06-01",
        support=9,
        confidence_ab=0.9,
        confidence_ba=0.1,
        structural="unexplained",
        dependency_kind=None,
    )
    assert CouplingEdgeResponse(**vars(e)).model_dump() == {
        "source": "a.py",
        "target": "b.py",
        "strength": 4.25,
        "last_co_change": "2026-06-01",
        "support": 9,
        "confidence_ab": 0.9,
        "confidence_ba": 0.1,
        "structural": "unexplained",
        "dependency_kind": None,
    }


def test_an_edge_from_an_older_index_still_serializes() -> None:
    """A repo indexed before the new fields existed must not 500 the endpoint."""
    e = CouplingEdge(source="a.py", target="b.py", strength=4.25, last_co_change=None)
    assert CouplingEdgeResponse(**vars(e)).model_dump() == {
        "source": "a.py",
        "target": "b.py",
        "strength": 4.25,
        "last_co_change": None,
        "support": 0,
        "confidence_ab": None,
        "confidence_ba": None,
        "structural": None,
        "dependency_kind": None,
    }


def test_a_corroborated_edge_names_the_dependency() -> None:
    """The kind rides beside the verdict: "explained" alone does not say how."""
    e = CouplingEdge(
        source="a.py",
        target="b.py",
        strength=4.25,
        last_co_change="2026-06-01",
        support=9,
        structural="corroborated",
        dependency_kind="type_use",
    )
    dumped = CouplingEdgeResponse(**vars(e)).model_dump()
    assert dumped["structural"] == "corroborated"
    assert dumped["dependency_kind"] == "type_use"
