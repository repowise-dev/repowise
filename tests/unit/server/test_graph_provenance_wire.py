"""The REST graph surfaces must carry the provenance the walk already computes.

``bfs_trace`` has filled ``hop_origins`` and ``termination`` since P15, and the
MCP tool reads both. The REST twin called the same function and passed neither,
so the dashboard rendered a trace that stops with no way to tell "the code ends
here" from "we could not follow it further" — the distinction the field exists
for. Same shape for ``resolution_origin`` on caller/callee rows, which shipped
a confidence float with nothing to explain it.

The three endpoints are covered together because they are one contract: the
web surfaces render all three through one vocabulary module.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphEdge, GraphNode, WikiSymbol
from tests.unit.server.conftest import create_test_repo

_FILE = "src/app/service.py"
_ENTRY = "src/app/service.py::handle"
_MID = "src/app/service.py::validate"
_LEAF = "src/app/service.py::persist"


async def _seed(session_factory, repo_id: str) -> None:
    """A two-hop chain, each hop stamped with a different origin.

    The origins differ so asserting the pair proves the field is per-edge and
    not a constant. Both are reliable execution evidence; ``global_unique`` is
    deliberately excluded from execution walks because it is only a name guess.
    """
    async with get_session(session_factory) as session:
        for node_id in (_ENTRY, _MID, _LEAF):
            session.add(
                GraphNode(
                    repository_id=repo_id,
                    node_id=node_id,
                    node_type="symbol",
                    language="python",
                    pagerank=0.5,
                    file_path=_FILE,
                    name=node_id.split("::")[-1],
                    kind="function",
                    community_meta_json='{"entry_point_score": 0.9}',
                )
            )
            session.add(
                WikiSymbol(
                    repository_id=repo_id,
                    file_path=_FILE,
                    symbol_id=node_id,
                    name=node_id.split("::")[-1],
                    qualified_name=node_id.split("::")[-1],
                    kind="function",
                    language="python",
                )
            )
        for src, tgt, origin in (
            (_ENTRY, _MID, "same_file"),
            (_MID, _LEAF, "import_scoped"),
        ):
            session.add(
                GraphEdge(
                    repository_id=repo_id,
                    source_node_id=src,
                    target_node_id=tgt,
                    imported_names_json="[]",
                    edge_type="calls",
                    confidence=0.95,
                    resolution_origin=origin,
                )
            )


@pytest.mark.asyncio
async def test_a_traced_flow_says_why_it_stopped(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(
        f"/api/graph/{repo['id']}/execution-flows",
        params={"entry_point": _ENTRY, "max_depth": 5},
    )
    assert resp.status_code == 200
    flow = resp.json()["flows"][0]

    assert flow["trace"] == [_ENTRY, _MID, _LEAF]
    # The leaf records no outgoing call, and the walk had budget left, so the
    # honest reason is that nothing was recorded — not that it was cut short.
    assert flow["termination"] == "no_callees"


@pytest.mark.asyncio
async def test_a_traced_flow_carries_an_origin_per_hop(client: AsyncClient, app) -> None:
    """`trace_via` is pairwise, so it is exactly one shorter than `trace`."""
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(
        f"/api/graph/{repo['id']}/execution-flows",
        params={"entry_point": _ENTRY, "max_depth": 5},
    )
    flow = resp.json()["flows"][0]

    assert flow["trace_via"] == ["same_file", "import_scoped"]
    assert len(flow["trace_via"]) == len(flow["trace"]) - 1


@pytest.mark.asyncio
async def test_a_depth_limited_flow_is_not_reported_as_an_ending(
    client: AsyncClient, app
) -> None:
    """The case the field exists for: the walk stopped, the code did not."""
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(
        f"/api/graph/{repo['id']}/execution-flows",
        params={"entry_point": _ENTRY, "max_depth": 1},
    )
    flow = resp.json()["flows"][0]

    assert flow["trace"] == [_ENTRY, _MID]
    assert flow["termination"] == "depth_limit"


@pytest.mark.asyncio
async def test_callers_callees_carry_the_origin(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(
        f"/api/graph/{repo['id']}/callers-callees", params={"symbol_id": _MID}
    )
    assert resp.status_code == 200
    body = resp.json()

    assert [c["resolution_origin"] for c in body["callers"]] == ["same_file"]
    assert [c["resolution_origin"] for c in body["callees"]] == ["import_scoped"]


@pytest.mark.asyncio
async def test_a_node_with_null_columns_degrades_instead_of_failing_the_response(
    client: AsyncClient, app
) -> None:
    """`name`, `kind` and `file_path` are nullable; the response fields are not.

    Found by this file's own fixture before it stamped a kind, then widened
    when a review pointed out the two siblings had the same gap. The bad row is
    one of up to twenty, so failing the whole call over it loses the nineteen
    that were fine. Each column is left null on its own node, so a failure
    names the column rather than the endpoint.
    """
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])
    bare = "src/app/other.py::mystery"  # kind null
    nameless = "src/app/other.py::anon"  # name null
    pathless = "src/app/other.py::floating"  # file_path null
    async with get_session(app.state.session_factory) as session:
        for node_id, name, kind, file_path in (
            (bare, "mystery", None, "src/app/other.py"),
            (nameless, None, "function", "src/app/other.py"),
            (pathless, "floating", "function", None),
        ):
            session.add(
                GraphNode(
                    repository_id=repo["id"],
                    node_id=node_id,
                    node_type="symbol",
                    language="python",
                    pagerank=0.1,
                    file_path=file_path,
                    name=name,
                    kind=kind,
                )
            )
            session.add(
                GraphEdge(
                    repository_id=repo["id"],
                    source_node_id=_MID,
                    target_node_id=node_id,
                    imported_names_json="[]",
                    edge_type="calls",
                    confidence=0.9,
                    resolution_origin="same_file",
                )
            )

    resp = await client.get(
        f"/api/graph/{repo['id']}/callers-callees", params={"symbol_id": _MID}
    )
    assert resp.status_code == 200
    rows = {c["symbol_id"]: c for c in resp.json()["callees"]}

    # Each falls back to what the node id itself can supply.
    assert rows[bare]["kind"] == "unknown"
    assert rows[nameless]["name"] == "anon"
    assert rows[pathless]["file"] == "src/app/other.py"


@pytest.mark.asyncio
async def test_symbol_detail_carries_the_origin(client: AsyncClient, app) -> None:
    """The drawer and the symbol route read different endpoints for one view."""
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(
        "/api/symbols/detail", params={"repo_id": repo["id"], "symbol_id": _MID}
    )
    assert resp.status_code == 200
    graph = resp.json()["graph"]

    assert [c["resolution_origin"] for c in graph["callers"]] == ["same_file"]
    assert [c["resolution_origin"] for c in graph["callees"]] == ["import_scoped"]
