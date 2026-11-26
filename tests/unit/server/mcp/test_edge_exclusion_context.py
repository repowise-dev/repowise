"""Stored graph rows for files the repo rules exclude stay out of the context paths.

A file excluded after it was indexed can keep its ``graph_edges`` and
``graph_nodes`` rows. These read paths must not publish it as a hop, a base
method, a middle function or a test, and must not count it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from repowise.core.exclusion import build_exclude_spec
from repowise.core.persistence.crud import get_cross_community_edges
from repowise.core.persistence.models import FunctionFact, GraphEdge, GraphNode, WikiSymbol
from repowise.server.mcp_server._graph_utils import bfs_trace
from repowise.server.mcp_server._symbol_facts import MAX_TEST_LINKS, symbol_card_facts
from repowise.server.mcp_server.tool_context.call_graph import _file_inbound_calls
from repowise.server.mcp_server.tool_context.interface_callers import interface_call_edges
from repowise.server.mcp_server.tool_context.sections import _layer_block
from repowise.server.mcp_server.tool_context.signals import _resolve_community, _resolve_metrics
from repowise.server.mcp_server.tool_symbol.callees import (
    _MAX_CALLEES_PER_HOP,
    _discover_callee_hops,
)

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)


def _spec(tmp_path):
    (tmp_path / ".repowise").mkdir(exist_ok=True)
    (tmp_path / ".repowise" / "config.yaml").write_text("exclude_patterns:\n- archive/**\n")
    spec = build_exclude_spec(tmp_path)
    assert spec
    return spec


def _node(session, repo_id, node_id, *, node_type="symbol", community_id=-1):
    session.add(
        GraphNode(
            repository_id=repo_id,
            node_id=node_id,
            node_type=node_type,
            name=node_id.split("::")[-1],
            file_path=node_id.split("::")[0],
            language="python",
            community_id=community_id,
            created_at=_NOW,
        )
    )


def _edge(session, repo_id, src, dst, edge_type="calls", conf=0.9, origin="import_scoped"):
    session.add(
        GraphEdge(
            repository_id=repo_id,
            source_node_id=src,
            target_node_id=dst,
            edge_type=edge_type,
            confidence=conf,
            resolution_origin=origin,
            created_at=_NOW,
        )
    )


def _symbol(session, repo_id, symbol_id):
    path, _, name = symbol_id.partition("::")
    session.add(
        WikiSymbol(
            repository_id=repo_id,
            file_path=path,
            symbol_id=symbol_id,
            name=name,
            qualified_name=name,
            kind="function",
            language="python",
        )
    )


@pytest.mark.asyncio
async def test_base_method_in_excluded_file_is_no_interface_hop(session, repo_id, tmp_path):
    impl = "src/impl.py::Impl.run"
    _node(session, repo_id, impl)
    _edge(session, repo_id, "archive/base.py::Base.run", impl, "dispatches_to", 0.75)
    _edge(session, repo_id, "src/use.py::go", "archive/base.py::Base.run")
    _edge(session, repo_id, "src/base.py::B.run", impl, "dispatches_to", 0.75)
    _edge(session, repo_id, "src/other.py::h", "src/base.py::B.run")
    await session.flush()
    spec = _spec(tmp_path)

    assert len(await interface_call_edges(session, repo_id, [impl])) == 2
    pairs = await interface_call_edges(session, repo_id, [impl], spec)
    assert [base for _, base in pairs] == ["src/base.py::B.run"]

    node = SimpleNamespace(node_id="src/impl.py", file_path="src/impl.py")
    _, by_file = await _file_inbound_calls(session, repo_id, node, spec)
    assert by_file == {"src/other.py": 1}


@pytest.mark.asyncio
async def test_excluded_importer_does_not_set_the_layer_role(session, repo_id, tmp_path):
    _edge(session, repo_id, "archive/old.py", "src/a.py", "imports", 1.0, None)
    await session.flush()
    layers = [
        SimpleNamespace(
            layer_id="layer:core",
            name="Core",
            description="",
            node_ids_json=json.dumps(["file:src/a.py"]),
        )
    ]

    async def role(spec):
        card = SimpleNamespace(
            session=session, repo_id=repo_id, data={}, query=SimpleNamespace(exclude_spec=spec)
        )
        await _layer_block(card, "src/a.py", layers)
        return card.data["architectural_layer"]["role"]

    assert await role(None) == "edge_connector"
    assert await role(_spec(tmp_path)) == "entry_point"


@pytest.mark.asyncio
async def test_symbol_facts_leave_out_excluded_callers_tests_and_middles(
    session, repo_id, tmp_path
):
    target = "src/t.py::run"
    _edge(session, repo_id, "src/c.py::a", target)
    _edge(session, repo_id, "archive/c.py::b", target)
    # Two-hop tests: one through a middle function in an excluded file.
    _edge(session, repo_id, "archive/mid.py::m", target)
    _edge(session, repo_id, "tests/test_y.py::test_b", "archive/mid.py::m")
    _edge(session, repo_id, "src/mid.py::n", target)
    _edge(session, repo_id, "tests/test_z.py::test_c", "src/mid.py::n")
    excluded_direct = [[f"archive/tests/test_x.py::t{i}", 1] for i in range(MAX_TEST_LINKS)]
    session.add(
        FunctionFact(
            repository_id=repo_id,
            symbol_id=target,
            awaits=False,
            is_generator=False,
            receiver_assigns_known=True,
            tests_json=json.dumps(
                [
                    *excluded_direct,
                    ["tests/test_d.py::test_d", 1],
                    ["tests/test_y.py::test_b", 2],
                    ["tests/test_z.py::test_c", 2],
                ]
            ),
        )
    )
    await session.flush()

    card = await symbol_card_facts(session, repo_id, target, _spec(tmp_path))

    assert "archive/" not in json.dumps(card)
    assert card["facts"].endswith("2 resolved callers")  # src/c.py::a and src/mid.py::n
    # The excluded direct tests sort first and do not take the allowed ones' slots.
    assert card["tests"] == [
        {"run": "tests/test_d.py::test_d"},
        {"run": "tests/test_z.py::test_c", "via": "src/mid.py::n"},
    ]


@pytest.mark.asyncio
async def test_callee_walk_never_advances_through_an_excluded_file(session, repo_id, tmp_path):
    root = "src/r.py::root"
    _edge(session, repo_id, root, "archive/shim.py::f", conf=0.99)
    _edge(session, repo_id, "archive/shim.py::f", "src/end.py::g")
    # Excluded callees outrank the allowed one and fill the per-node cap.
    for i in range(_MAX_CALLEES_PER_HOP):
        _edge(session, repo_id, root, f"archive/x{i:02d}.py::f", conf=0.98)
    _edge(session, repo_id, root, "src/b.py::h", conf=0.8)
    for sid in (root, "archive/shim.py::f", "src/end.py::g", "src/b.py::h"):
        _symbol(session, repo_id, sid)
    await session.flush()

    hops = await _discover_callee_hops(
        session, repo_id, SimpleNamespace(symbol_id=root), 3, _spec(tmp_path)
    )

    assert [(h.number, [r.symbol_id for r in h.rows]) for h in hops] == [(1, ["src/b.py::h"])]


@pytest.mark.asyncio
async def test_execution_flow_never_steps_into_an_excluded_file(session, repo_id, tmp_path):
    entry = "src/e.py::main"
    _edge(session, repo_id, entry, "archive/x.py::f", conf=0.95)
    _edge(session, repo_id, entry, "src/y.py::g", conf=0.8)
    await session.flush()

    assert await bfs_trace(session, repo_id, entry, 3) == [entry, "archive/x.py::f"]
    trace = await bfs_trace(session, repo_id, entry, 3, exclude_spec=_spec(tmp_path))
    assert trace == [entry, "src/y.py::g"]


@pytest.mark.asyncio
async def test_excluded_callees_do_not_fill_the_flow_fetch_cut(session, repo_id, tmp_path):
    from repowise.server.mcp_server._graph_utils import _CALLEE_FETCH_LIMIT

    entry = "src/e.py::main"
    for i in range(_CALLEE_FETCH_LIMIT + 5):
        _edge(session, repo_id, entry, f"archive/x{i}.py::f", conf=0.95)
    _edge(session, repo_id, entry, "src/y.py::g", conf=0.6)
    await session.flush()

    trace = await bfs_trace(session, repo_id, entry, 3, exclude_spec=_spec(tmp_path))
    assert trace == [entry, "src/y.py::g"]


@pytest.mark.asyncio
async def test_degrees_and_community_counts_skip_excluded_endpoints(session, repo_id, tmp_path):
    repository = SimpleNamespace(id=repo_id)
    _node(session, repo_id, "src/a.py", node_type="file", community_id=1)
    _node(session, repo_id, "archive/y.py", node_type="file", community_id=1)
    _node(session, repo_id, "src/b.py", node_type="file", community_id=2)
    _node(session, repo_id, "archive/x.py", node_type="file", community_id=3)
    _edge(session, repo_id, "src/b.py", "src/a.py", "imports", 1.0, None)
    _edge(session, repo_id, "archive/x.py", "src/a.py", "imports", 1.0, None)
    _edge(session, repo_id, "src/a.py", "src/b.py", "imports", 1.0, None)
    _edge(session, repo_id, "src/a.py", "archive/x.py", "imports", 1.0, None)
    _edge(session, repo_id, "src/a.py", "archive/x.py", "type_use", 1.0, None)
    _edge(session, repo_id, "archive/y.py", "src/b.py", "imports", 1.0, None)
    await session.flush()
    spec = _spec(tmp_path)

    raw = await get_cross_community_edges(session, repo_id, 1)
    assert raw == [
        {"target_community_id": 3, "edge_count": 2},
        {"target_community_id": 2, "edge_count": 2},
    ] or raw == [
        {"target_community_id": 2, "edge_count": 2},
        {"target_community_id": 3, "edge_count": 2},
    ]
    assert await get_cross_community_edges(session, repo_id, 1, exclude_spec=spec) == [
        {"target_community_id": 2, "edge_count": 1}
    ]

    data: dict = {}
    await _resolve_community(session, repository, "src/a.py", data, exclude_spec=spec)
    assert [(n["id"], n["cross_edges"]) for n in data["community"]["neighbors"]] == [(2, 1)]
    assert "archive/" not in json.dumps(data)

    data = {}
    await _resolve_metrics(session, repository, "src/a.py", data)
    assert (data["metrics"]["in_degree"], data["metrics"]["out_degree"]) == (2, 3)
    data = {}
    await _resolve_metrics(session, repository, "src/a.py", data, exclude_spec=spec)
    assert (data["metrics"]["in_degree"], data["metrics"]["out_degree"]) == (1, 1)
