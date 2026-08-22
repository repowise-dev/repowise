from types import SimpleNamespace

from repowise.server.routers.graph.full_graph import _flow_member_ids


def _edge(source: str, target: str, edge_type: str, **attrs):
    return SimpleNamespace(
        source_node_id=source,
        target_node_id=target,
        edge_type=edge_type,
        confidence=attrs.get("confidence", 0.9),
        resolution_origin=attrs.get("resolution_origin"),
    )


def test_export_flow_reservation_uses_the_shared_execution_policy():
    edges = [
        _edge("src/a.py::run", "src/b.py::base", "calls"),
        _edge(
            "src/b.py::base",
            "src/c.py::impl",
            "dispatches_to",
            resolution_origin="framework",
        ),
        _edge(
            "src/b.py::base",
            "src/guess.py::wrong",
            "calls",
            confidence=1.0,
            resolution_origin="global_unique",
        ),
    ]
    assert _flow_member_ids(edges, ["src/a.py::run"]) == {
        "src/a.py",
        "src/b.py",
        "src/c.py",
    }
