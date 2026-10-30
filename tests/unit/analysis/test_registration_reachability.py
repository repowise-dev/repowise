# tests/unit/analysis/test_registration_reachability.py
import networkx as nx
import pytest

from repowise.core.analysis.test_reach.walk import registration_walk
from repowise.core.analysis.test_reach.maps import Need


@pytest.fixture
def graph_with_registration() -> nx.DiGraph:
    """Grafo de ejemplo:
    - test_user.py  --imports-->  server/app.py
    - server/app.py --registra--> server/routers/user.py
    """
    g = nx.DiGraph()
    g.add_node("tests/test_user.py", type="file")
    g.add_node("server/app.py", type="file")
    g.add_node("server/routers/user.py", type="file")

    g.add_edge("tests/test_user.py", "server/app.py", edge_type="imports")
    g.add_edge("server/app.py", "server/routers/user.py",
               edge_type="registration")
    return g


def _walk_with_graph(graph: nx.DiGraph, seeds: list[str]):
    """Ejecuta el registration_walk usando el grafo como fuente de Need."""
    gen = registration_walk(seeds, test_files={"tests/test_user.py"},
                            max_depth=3)
    need = next(gen)
    while True:
        try:
            if need.kind == "registrations":
                pairs = [
                    (u, v) for u, v, d in graph.edges(data=True)
                    if d.get("edge_type") == "registration"
                    and u in need.keys
                ]
                need = gen.send(pairs)
            else:
                need = gen.send([])
        except StopIteration as exc:
            return exc.value


def test_router_is_reached_when_app_factory_is_reached(graph_with_registration):
    seeds = ["server/app.py"]
    reached = _walk_with_graph(graph_with_registration, seeds)

    assert "server/routers/user.py" in reached
    # El router está al mismo nivel que el app factory (distancia 0).
    assert reached["server/routers/user.py"] == reached["server/app.py"]


def test_router_not_reached_without_registration_edge(graph_with_registration):
    graph_with_registration.remove_edge("server/app.py",
                                        "server/routers/user.py")
    seeds = ["server/app.py"]
    reached = _walk_with_graph(graph_with_registration, seeds)

    assert "server/routers/user.py" not in reached
