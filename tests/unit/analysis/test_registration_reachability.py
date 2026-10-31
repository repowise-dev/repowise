# tests/unit/analysis/test_registration_reachability.py
"""Test unitario para el Issue #4933: código alcanzado vía registro."""
from __future__ import annotations

import networkx as nx
import pytest

from repowise.core.analysis.test_reach.maps import (
    ImportGraph,
    WalkMaps,
    run,
)
from repowise.core.analysis.test_reach.walk import (
    import_walk,
    registration_walk,
)


@pytest.fixture
def graph_with_registration() -> nx.DiGraph:
    """Grafo de ejemplo:
    - tests/test_user.py  --imports-->  server/app.py
    - server/app.py       --registration-->  server/routers/user.py
    """
    g = nx.DiGraph()
    g.add_node("tests/test_user.py", node_type="file", is_test=True)
    g.add_node("server/app.py", node_type="file")
    g.add_node("server/routers/user.py", node_type="file")

    g.add_edge("tests/test_user.py", "server/app.py", edge_type="imports")
    g.add_edge(
        "server/app.py",
        "server/routers/user.py",
        edge_type="registration",
    )
    return g


@pytest.fixture
def maps_from_graph(graph_with_registration: nx.DiGraph) -> WalkMaps:
    # Construimos WalkMaps a mano para no depender de build_walk_maps
    # (que require EXECUTION_EDGE_TYPES/FILE_DEPENDENCY_EDGE_TYPES).
    return WalkMaps(
        declares={},
        callers={},
        imports=ImportGraph({"server/app.py": ("tests/test_user.py",)}),
        files=frozenset(graph_with_registration.nodes),
        test_files=frozenset({"tests/test_user.py"}),
        registrations={"server/app.py": ("server/routers/user.py",)},
    )


def test_router_is_reached_when_app_factory_is_reached(maps_from_graph):
    """El test importa el app factory; éste registra el router.

    El router debe marcarse como alcanzado, con los mismos tests que el
    factory (mismo nivel).
    """
    # 1) Import walk: el test llega a server/app.py.
    import_reach = run(
        import_walk(["server/app.py"], maps_from_graph.test_files),
        maps_from_graph,
    )
    assert "tests/test_user.py" in import_reach["server/app.py"]

    # 2) Registration walk con el resultado anterior como prior.
    reg_reach = run(
        registration_walk(
            ["server/app.py"],
            maps_from_graph.test_files,
            prior=import_reach,
        ),
        maps_from_graph,
    )

    assert "server/routers/user.py" in reg_reach
    assert "tests/test_user.py" in reg_reach["server/routers/user.py"]
    # Mismo nivel (mismos tests) que el registrador.
    assert reg_reach["server/routers/user.py"] == reg_reach["server/app.py"]


def test_router_not_reached_without_registration_edge(maps_from_graph):
    """Sin la arista de registro, el router no debe marcarse como alcanzado."""
    maps_no_reg = WalkMaps(
        declares=maps_from_graph.declares,
        callers=maps_from_graph.callers,
        imports=maps_from_graph.imports,
        files=maps_from_graph.files,
        test_files=maps_from_graph.test_files,
        registrations=None,  # P2: mapa opcional -> debe tratarse como vacío.
    )

    import_reach = run(
        import_walk(["server/app.py"], maps_no_reg.test_files),
        maps_no_reg,
    )
    reg_reach = run(
        registration_walk(
            ["server/app.py"],
            maps_no_reg.test_files,
            prior=import_reach,
        ),
        maps_no_reg,
    )

    assert "server/routers/user.py" not in reg_reach


def test_need_registrations_is_safe_when_mapping_is_none(maps_from_graph):
    """P2: ``Need("registrations", ...)`` con ``registrations=None`` responde [].

    No debe lanzar ``AttributeError`` ni ``TypeError``.
    """
    from repowise.core.analysis.test_reach.maps import Need

    maps_no_reg = WalkMaps(
        declares=maps_from_graph.declares,
        callers=maps_from_graph.callers,
        imports=maps_from_graph.imports,
        files=maps_from_graph.files,
        test_files=maps_from_graph.test_files,
        registrations=None,
    )
    answer = maps_no_reg.answer(Need("registrations", ("server/app.py",)))
    assert answer == []
