"""`/api/symbols/detail` must name each relation, not flatten them into calls.

The endpoint served the whole of ``SYMBOL_USE_EDGE_TYPES`` under one
confidence-ranked 40-row cap, so a subclass was presented as a caller and, on
a base class, evicted the real callers entirely. Measured on the live django
index before the fix: ``Model`` has 8 callers and 1,516 subclasses and the cut
served 39 subclasses and 1 caller; ``TestCase`` has 3 callers and 868
subclasses and served none of its callers.

These tests drive the real route, so they fail if the endpoint stops emitting
the key rather than only if the renderer stops reading it. The heritage
section had been dead for its whole life precisely because nothing asserted
the server end.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphEdge, GraphNode, WikiSymbol, _new_uuid
from tests.unit.server.conftest import create_test_repo

BASE = "app/models.py::Model"


async def _seed(session_factory, repo_id: str) -> None:
    """A base class with 2 callers and 60 subclasses, plus one framework bind.

    60 exceeds the 40-row call cap on purpose: under the old shared cut the
    two callers could not be served at all.
    """
    async with get_session(session_factory) as session:
        session.add(
            WikiSymbol(
                id=_new_uuid(),
                repository_id=repo_id,
                file_path="app/models.py",
                symbol_id=BASE,
                name="Model",
                qualified_name="app.models.Model",
                kind="class",
                signature="class Model",
                start_line=1,
                end_line=10,
                visibility="public",
                language="python",
            )
        )
        nodes = [BASE, "app/views.py::save", "app/views.py::delete", "app/wire.py::container"]
        nodes += [f"app/sub{i}.py::Sub{i}" for i in range(60)]
        for node_id in nodes:
            session.add(
                GraphNode(
                    id=_new_uuid(),
                    repository_id=repo_id,
                    node_id=node_id,
                    node_type="symbol",
                    name=node_id.split("::")[-1],
                    file_path=node_id.split("::")[0],
                    kind="class" if "Sub" in node_id else "function",
                    start_line=1,
                )
            )

        def edge(source: str, edge_type: str, confidence: float) -> GraphEdge:
            return GraphEdge(
                id=_new_uuid(),
                repository_id=repo_id,
                source_node_id=source,
                target_node_id=BASE,
                edge_type=edge_type,
                confidence=confidence,
            )

        # Subclasses outrank the callers on confidence, which is what made the
        # shared cut drop the callers rather than merely reorder them.
        for i in range(60):
            session.add(edge(f"app/sub{i}.py::Sub{i}", "extends", 0.99))
        session.add(edge("app/views.py::save", "calls", 0.5))
        session.add(edge("app/views.py::delete", "calls", 0.5))
        session.add(edge("app/wire.py::container", "framework_binds", 0.9))


async def _detail(client: AsyncClient, repo_id: str) -> dict:
    resp = await client.get(
        "/api/symbols/detail", params={"repo_id": repo_id, "symbol_id": BASE}
    )
    assert resp.status_code == 200
    return resp.json()["graph"]


@pytest.mark.asyncio
async def test_callers_are_calls_only(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    graph = await _detail(client, repo["id"])

    assert {r["edge_type"] for r in graph["callers"]} == {"calls"}
    assert graph["callees"] == []


@pytest.mark.asyncio
async def test_heritage_cannot_evict_the_real_callers(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    graph = await _detail(client, repo["id"])

    # 60 higher-confidence subclasses used to consume the whole 40-row cut.
    assert len(graph["callers"]) == 2
    assert graph["caller_total"] == 2
    assert {r["name"] for r in graph["callers"]} == {"save", "delete"}


@pytest.mark.asyncio
async def test_relations_carry_each_kind_with_its_true_total(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    graph = await _detail(client, repo["id"])

    by_type = {(g["direction"], g["edge_type"]): g for g in graph["relations"]}
    assert set(by_type) == {("in", "extends"), ("in", "framework_binds")}

    extends = by_type[("in", "extends")]
    assert extends["group"] == "heritage"
    assert extends["total"] == 60, "the total must be the count, not the row cap"
    assert 0 < len(extends["rows"]) <= 10
    assert {r["edge_type"] for r in extends["rows"]} == {"extends"}

    assert by_type[("in", "framework_binds")]["group"] == "wiring"
    assert by_type[("in", "framework_binds")]["total"] == 1


@pytest.mark.asyncio
async def test_degree_counts_every_relation_kind(client: AsyncClient, app) -> None:
    """Degree stays a graph metric; `caller_total` is the caller count."""
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    graph = await _detail(client, repo["id"])

    assert graph["in_degree"] == 63  # 60 extends + 2 calls + 1 framework_binds
    assert graph["out_degree"] == 0
    assert graph["caller_total"] == 2


@pytest.mark.asyncio
async def test_a_recursive_call_appears_on_both_sides(client: AsyncClient, app) -> None:
    """A self-edge is counted once per direction, so it must be served twice.

    Served once, its row is a permanent "+1 more" under Calls that no paging
    can reach.
    """
    repo = await create_test_repo(client)
    repo_id = repo["id"]
    await _seed(app.state.session_factory, repo_id)
    async with get_session(app.state.session_factory) as session:
        session.add(
            GraphEdge(
                id=_new_uuid(),
                repository_id=repo_id,
                source_node_id=BASE,
                target_node_id=BASE,
                edge_type="calls",
                confidence=0.9,
            )
        )

    graph = await _detail(client, repo_id)

    assert graph["callee_total"] == len(graph["callees"]) == 1
    assert graph["callees"][0]["symbol_id"] == BASE
    assert graph["caller_total"] == len(graph["callers"]) == 3
