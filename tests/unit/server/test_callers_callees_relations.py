"""`/callers-callees` must agree with `/api/symbols/detail`.

The drawer this endpoint feeds and the routed symbol page render the same
component, and they disagreed: the page named every relation kind after #1660
while the drawer had only callers and callees. Both now go through
`load_symbol_relations`, so a divergence is a shared-helper change rather than
a drift between two hand-written loops.

Also pinned here: `caller_count` is the true total. It was `len(callers)`,
capped at `limit`, and `symbol-drawer-wrapper.tsx` renders it as
`caller_total` — so a symbol with 275 callers reported 20.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphEdge, GraphNode, WikiSymbol, _new_uuid
from tests.unit.server.conftest import create_test_repo

BASE = "app/models.py::Model"


async def _seed(session_factory, repo_id: str) -> None:
    """A base class with 30 callers, 60 subclasses and one framework bind."""
    async with get_session(session_factory) as session:
        # `/api/symbols/detail` resolves the symbol row first, so the
        # cross-surface comparison below needs one.
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
        nodes = [BASE, "app/wire.py::container"]
        nodes += [f"app/sub{i}.py::Sub{i}" for i in range(60)]
        nodes += [f"app/call{i}.py::call_{i}" for i in range(30)]
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

        def edge(source: str, edge_type: str, confidence: float) -> None:
            session.add(
                GraphEdge(
                    id=_new_uuid(),
                    repository_id=repo_id,
                    source_node_id=source,
                    target_node_id=BASE,
                    edge_type=edge_type,
                    confidence=confidence,
                )
            )

        for i in range(60):
            edge(f"app/sub{i}.py::Sub{i}", "extends", 0.99)
        # 0.5 on purpose: this surface applies no confidence floor, while the
        # MCP one filters below 0.7. So these 30 are 30 callers here and zero
        # to an agent — a real remaining divergence (backlog B8), pinned here
        # rather than left to be discovered as a bug.
        for i in range(30):
            edge(f"app/call{i}.py::call_{i}", "calls", 0.5)
        edge("app/wire.py::container", "framework_binds", 0.9)


async def _fetch(client: AsyncClient, repo_id: str, **params) -> dict:
    resp = await client.get(
        f"/api/graph/{repo_id}/callers-callees",
        params={"symbol_id": BASE, **params},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_callers_are_calls_only(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    body = await _fetch(client, repo["id"])

    assert {r["edge_type"] for r in body["callers"]} == {"calls"}
    assert body["callees"] == []


@pytest.mark.asyncio
async def test_caller_count_is_the_true_total_not_the_row_cap(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    body = await _fetch(client, repo["id"], limit=20)

    assert len(body["callers"]) == 20, "rows honour the request limit"
    assert body["caller_count"] == 30, "the count is the truth, not the page size"
    assert body["truncated"] is True


@pytest.mark.asyncio
async def test_relations_carry_the_kinds_the_drawer_was_missing(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    body = await _fetch(client, repo["id"])

    by_type = {(g["direction"], g["edge_type"]): g for g in body["relations"]}
    assert set(by_type) == {("in", "extends"), ("in", "framework_binds")}
    assert by_type[("in", "extends")]["group"] == "heritage"
    assert by_type[("in", "extends")]["total"] == 60
    assert by_type[("in", "framework_binds")]["group"] == "wiring"


@pytest.mark.asyncio
async def test_the_drawer_and_the_symbol_page_agree(client: AsyncClient, app) -> None:
    """The whole point of sharing the helper, asserted rather than assumed."""
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    drawer = await _fetch(client, repo["id"], limit=40)
    resp = await client.get(
        "/api/symbols/detail", params={"repo_id": repo["id"], "symbol_id": BASE}
    )
    page = resp.json()["graph"]

    assert drawer["caller_count"] == page["caller_total"]
    assert [(g["direction"], g["edge_type"], g["total"]) for g in drawer["relations"]] == [
        (g["direction"], g["edge_type"], g["total"]) for g in page["relations"]
    ]


@pytest.mark.asyncio
async def test_edge_types_filters_relations(client: AsyncClient, app) -> None:
    """The param no longer decides what counts as a caller."""
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    body = await _fetch(client, repo["id"], edge_types="extends")

    assert {g["edge_type"] for g in body["relations"]} == {"extends"}
    assert {r["edge_type"] for r in body["callers"]} == {"calls"}
