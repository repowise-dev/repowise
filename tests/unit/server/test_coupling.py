"""Wire-shape tests for the coupling router serializers.

The graph assembly itself is covered by ``tests/unit/coupling/test_graph.py``;
here we lock the JSON keys the endpoint emits so the wire contract (mirrored in
``packages/types/src/coupling.ts``) cannot drift silently.
"""

from __future__ import annotations

from repowise.core.analysis.coupling import CouplingEdge, CouplingNode
from repowise.server.routers.coupling import _edge_to_dict, _node_to_dict


def test_node_to_dict_wire_shape() -> None:
    n = CouplingNode(file_path="a.py", module="api", score=3.4, nloc=420)
    assert _node_to_dict(n) == {
        "file_path": "a.py",
        "module": "api",
        "score": 3.4,
        "nloc": 420,
    }


def test_node_to_dict_null_score_module() -> None:
    n = CouplingNode(file_path="config.yaml", module=None, score=None, nloc=0)
    assert _node_to_dict(n) == {
        "file_path": "config.yaml",
        "module": None,
        "score": None,
        "nloc": 0,
    }


def test_edge_to_dict_wire_shape() -> None:
    e = CouplingEdge(source="a.py", target="b.py", strength=4.25, last_co_change="2026-06-01")
    assert _edge_to_dict(e) == {
        "source": "a.py",
        "target": "b.py",
        "strength": 4.25,
        "last_co_change": "2026-06-01",
    }
