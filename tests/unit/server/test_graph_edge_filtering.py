"""The graph-reading REST surfaces must read dependency edges, not every row.

Four endpoints loaded ``graph_edges`` with no filter and presented the result
as dependencies. The table also carries containment (``defines`` is file →
symbol, ``has_method`` is class → member) and the temporal ``co_changes``
relation, so:

* a file's "dependencies" list contained the file's own functions;
* a symbol's "callers" list contained the file that declares it;
* ``/path`` reported a dependency path across a co-change hop, the same defect
  ``get_dependency_path`` was fixed for in #1470 — its nine-line comment did
  not travel to the REST twin;
* ``/ego`` made a co-change partner a one-hop neighbour.

The degree numbers are covered too, because each is rendered beside the list it
disagreed with: the file page prints ``in_degree`` as "Dependents (N)" directly
above the dependents list.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphEdge, GraphNode, WikiSymbol
from tests.unit.server.conftest import create_test_repo

_FILE = "src/app/service.py"
_OTHER = "src/app/repo.py"
_PARTNER = "docs/changelog.md"
_SYMBOL = "src/app/service.py::Service"
_METHOD = "src/app/service.py::Service.run"
_CALLER = "src/app/repo.py::load"


async def _seed(session_factory, repo_id: str) -> None:
    """One file with a symbol, one real import, one co-change partner.

    Deliberately minimal: every edge below is one of the three kinds under
    test, so a failure names the kind rather than a fixture.
    """
    async with get_session(session_factory) as session:
        for node_id, node_type in (
            (_FILE, "file"),
            (_OTHER, "file"),
            (_PARTNER, "file"),
            (_SYMBOL, "symbol"),
            (_METHOD, "symbol"),
            (_CALLER, "symbol"),
        ):
            session.add(
                GraphNode(
                    repository_id=repo_id,
                    node_id=node_id,
                    node_type=node_type,
                    language="python",
                    pagerank=0.1,
                    file_path=node_id.split("::")[0] if node_type == "symbol" else None,
                    name=node_id.split("::")[-1] if node_type == "symbol" else None,
                )
            )
        # /api/symbols/detail resolves the symbol row first, so the graph node
        # alone is not enough to reach the callers block under test.
        for symbol_id, name, kind in (
            (_SYMBOL, "Service", "class"),
            (_METHOD, "run", "method"),
        ):
            session.add(
                WikiSymbol(
                    repository_id=repo_id,
                    file_path=_FILE,
                    symbol_id=symbol_id,
                    name=name,
                    qualified_name=name,
                    kind=kind,
                    language="python",
                )
            )
        for src, tgt, etype in (
            (_FILE, _OTHER, "imports"),          # the one real dependency
            (_FILE, _SYMBOL, "defines"),         # containment, file -> symbol
            (_FILE, _PARTNER, "co_changes"),     # temporal
            (_CALLER, _SYMBOL, "calls"),         # the one real caller
            (_SYMBOL, _METHOD, "has_method"),    # containment, class -> member
            (_CALLER, _METHOD, "calls"),         # the method's one real caller
        ):
            session.add(
                GraphEdge(
                    repository_id=repo_id,
                    source_node_id=src,
                    target_node_id=tgt,
                    imported_names_json="[]",
                    edge_type=etype,
                )
            )


@pytest.mark.asyncio
async def test_a_file_does_not_depend_on_its_own_symbols(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/files/{_FILE}")
    assert resp.status_code == 200
    graph = resp.json()["graph"]

    deps = [d["node_id"] for d in graph["dependencies"]]
    assert _SYMBOL not in deps, f"file depends on its own symbol: {deps}"
    assert _PARTNER not in deps, f"co-change partner served as a dependency: {deps}"
    assert deps == [_OTHER]


@pytest.mark.asyncio
async def test_the_file_dependent_count_matches_the_dependent_list(
    client: AsyncClient, app
) -> None:
    """The UI prints this count as "Dependents (N)" above that list."""
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/files/{_FILE}")
    graph = resp.json()["graph"]

    assert graph["out_degree"] == len(graph["dependencies"])
    assert graph["in_degree"] == len(graph["dependents"])


@pytest.mark.asyncio
async def test_the_two_degree_blocks_on_the_file_response_agree(
    client: AsyncClient, app
) -> None:
    """One response carries this number twice, under two different keys.

    ``health.signals`` renders as "N files depend on this" and ``graph`` as
    "Dependents (N)". They are built by different helpers, so scoping one and
    not the other makes a single page contradict itself.
    """
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/files/{_FILE}")
    body = resp.json()

    assert body["health"]["signals"]["out_degree"] == body["graph"]["out_degree"]
    assert body["health"]["signals"]["in_degree"] == body["graph"]["in_degree"]
    # And the shared value is the dependency one, not every adjacent row.
    assert body["graph"]["out_degree"] == 1


@pytest.mark.asyncio
async def test_symbol_degree_agrees_between_the_page_and_the_drawer(
    client: AsyncClient, app
) -> None:
    """``/graph/{repo}/metrics`` feeds the drawer, ``/symbols/detail`` the page.

    Both render through one component, so one symbol must not report two
    different degrees depending on which the user opened.
    """
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    page = await client.get(
        "/api/symbols/detail", params={"repo_id": repo["id"], "symbol_id": _SYMBOL}
    )
    drawer = await client.get(
        f"/api/graph/{repo['id']}/metrics", params={"node_id": _SYMBOL}
    )
    assert page.status_code == 200
    assert drawer.status_code == 200

    assert drawer.json()["in_degree"] == page.json()["graph"]["in_degree"]
    assert drawer.json()["out_degree"] == page.json()["graph"]["out_degree"]
    assert drawer.json()["in_degree"] == 1  # the caller, not the declaring file


@pytest.mark.asyncio
async def test_a_symbols_declaring_file_is_not_a_caller(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(
        "/api/symbols/detail", params={"repo_id": repo["id"], "symbol_id": _SYMBOL}
    )
    assert resp.status_code == 200
    graph = resp.json()["graph"]

    callers = [c["symbol_id"] for c in graph["callers"]]
    assert _FILE not in callers, f"declaring file served as a caller: {callers}"
    assert callers == [_CALLER]
    assert graph["in_degree"] == len(callers)


@pytest.mark.asyncio
async def test_a_methods_declaring_class_is_not_a_caller(
    client: AsyncClient, app
) -> None:
    """The ``has_method`` half of containment, class -> member."""
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(
        "/api/symbols/detail", params={"repo_id": repo["id"], "symbol_id": _METHOD}
    )
    assert resp.status_code == 200
    graph = resp.json()["graph"]

    callers = [c["symbol_id"] for c in graph["callers"]]
    assert _SYMBOL not in callers, f"declaring class served as a caller: {callers}"
    assert callers == [_CALLER]


@pytest.mark.asyncio
async def test_rest_path_does_not_walk_a_co_change_hop(
    client: AsyncClient, app
) -> None:
    """The REST twin of the MCP tool fixed in #1470."""
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(
        f"/api/graph/{repo['id']}/path", params={"from": _FILE, "to": _PARTNER}
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["path"] == [], f"co_changes walked as a dependency: {body['path']}"
    # Shortest-path must be what rejected the hop. If the node had instead
    # fallen out of the graph the endpoint would 404, which would pass a
    # naive "no path" assertion for entirely the wrong reason.
    assert "not found in graph" not in (body.get("explanation") or "")


@pytest.mark.asyncio
async def test_path_still_resolves_through_the_symbol_layer(
    client: AsyncClient, app
) -> None:
    """``defines`` is the only bridge from a file to its symbols.

    Nothing points from a symbol back to a file, so excluding containment
    cannot suppress a false file-to-file path — it only deletes the real
    file -> symbol answer.
    """
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(
        f"/api/graph/{repo['id']}/path", params={"from": _FILE, "to": _SYMBOL}
    )
    assert resp.status_code == 200
    assert resp.json()["path"] == [_FILE, _SYMBOL]


@pytest.mark.asyncio
async def test_ego_graph_excludes_a_co_change_partner(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(
        f"/api/graph/{repo['id']}/ego", params={"node_id": _FILE, "hops": 1}
    )
    assert resp.status_code == 200
    body = resp.json()

    node_ids = {n["node_id"] for n in body["nodes"]}
    assert _PARTNER not in node_ids, "co-change partner is not a graph neighbour"
    assert _OTHER in node_ids
    assert body["outbound_count"] == 2  # imports + defines, not the co-change
