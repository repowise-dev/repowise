"""Pins every response of `/metrics`, `/callers-callees` and `/execution-flows`.

Covers exact and fuzzy symbol resolution, direction and edge-type filters, row
caps, entry-point selection and the 404 paths, on a small fixture graph.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphEdge, GraphNode
from tests.unit.server.conftest import create_test_repo

_MAIN = "src/a.py::main"
_A_HELPER = "src/a.py::helper"
_B_HELPER = "src/b.py::Tool.helper"
_LEAF = "src/b.py::leaf"
_BASE = "src/c.py::Base"
_CHILD = "src/c.py::Child"


def _symbol(node_id: str, name: str, kind: str, **extra) -> GraphNode:
    return GraphNode(
        node_id=node_id,
        node_type="symbol",
        language="python",
        name=name,
        kind=kind,
        file_path=node_id.split("::")[0],
        start_line=3,
        **extra,
    )


def _nodes() -> list[GraphNode]:
    files = [
        GraphNode(
            node_id=path,
            node_type="file",
            language="python",
            file_path=path,
            pagerank=pr,
            betweenness=bw,
            community_id=cid,
        )
        for path, pr, bw, cid in (
            ("src/a.py", 0.4, 0.2, 1),
            ("src/b.py", 0.3, 0.1, 2),
            ("src/c.py", 0.1, 0.0, 2),
        )
    ]
    main_meta = '{"label": "entry", "entry_point_score": 0.91234}'
    return [
        *files,
        _symbol(
            _MAIN,
            "main",
            "function",
            pagerank=0.35,
            betweenness=0.15,
            betweenness_commit="abc123",
            community_id=1,
            community_meta_json=main_meta,
            is_entry_point=True,
            signature="def main()",
        ),
        _symbol(
            _A_HELPER,
            "helper",
            "function",
            community_id=1,
            community_meta_json='{"entry_point_score": 0.4}',
        ),
        _symbol(_B_HELPER, "helper", "method", community_id=2),
        _symbol(_LEAF, "leaf", "function", community_id=2),
        _symbol(_BASE, "Base", "class", community_id=2),
        _symbol(_CHILD, "Child", "class", community_id=2),
    ]


_EDGES = (
    ("src/a.py", "src/b.py", "imports", None),
    (_MAIN, _A_HELPER, "calls", "same_file"),
    (_A_HELPER, _LEAF, "calls", "import_scoped"),
    (_B_HELPER, _LEAF, "calls", "same_file"),
    (_MAIN, _B_HELPER, "calls", "import_scoped"),
    (_CHILD, _BASE, "extends", None),
)


async def _seed(session_factory, repo_id: str) -> None:
    async with get_session(session_factory) as session:
        for node in _nodes():
            node.repository_id = repo_id
            session.add(node)
        for src, tgt, edge_type, origin in _EDGES:
            session.add(
                GraphEdge(
                    repository_id=repo_id,
                    source_node_id=src,
                    target_node_id=tgt,
                    imported_names_json="[]",
                    edge_type=edge_type,
                    confidence=0.9,
                    resolution_origin=origin,
                )
            )


CASES: list[tuple[str, str, dict]] = [
    ("metrics_file", "metrics", {"node_id": "src/a.py"}),
    ("metrics_symbol", "metrics", {"node_id": _MAIN}),
    ("metrics_missing", "metrics", {"node_id": "src/nope.py"}),
    ("cc_both", "callers-callees", {"symbol_id": _A_HELPER}),
    ("cc_callers", "callers-callees", {"symbol_id": _A_HELPER, "direction": "callers"}),
    ("cc_callees", "callers-callees", {"symbol_id": _A_HELPER, "direction": "callees"}),
    ("cc_bad_direction", "callers-callees", {"symbol_id": _A_HELPER, "direction": "up"}),
    ("cc_limit", "callers-callees", {"symbol_id": _LEAF, "limit": 1}),
    ("cc_bare_name", "callers-callees", {"symbol_id": "helper"}),
    ("cc_file_hint", "callers-callees", {"symbol_id": "src/b.py::helper"}),
    ("cc_hint_miss", "callers-callees", {"symbol_id": "src/z.py::helper"}),
    ("cc_file_node", "callers-callees", {"symbol_id": "src/a.py"}),
    ("cc_missing", "callers-callees", {"symbol_id": "src/a.py::nope"}),
    ("cc_relations", "callers-callees", {"symbol_id": _BASE}),
    ("cc_edge_filter_hit", "callers-callees", {"symbol_id": _BASE, "edge_types": "extends"}),
    ("cc_edge_filter_miss", "callers-callees", {"symbol_id": _BASE, "edge_types": "x, "}),
    ("cc_relations_callees", "callers-callees", {"symbol_id": _BASE, "direction": "callees"}),
    ("flows_top", "execution-flows", {}),
    ("flows_top_one", "execution-flows", {"top_n": 1, "max_depth": 1}),
    ("flows_entry", "execution-flows", {"entry_point": _MAIN}),
    ("flows_entry_leaf", "execution-flows", {"entry_point": _LEAF}),
    ("flows_missing", "execution-flows", {"entry_point": "src/a.py::nope"}),
]


async def capture(client: AsyncClient, app) -> dict[str, tuple[int, object]]:
    """Every case's status and body, plus flows on a repo with no graph."""
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])
    out: dict[str, tuple[int, object]] = {}
    for name, endpoint, params in CASES:
        resp = await client.get(f"/api/graph/{repo['id']}/{endpoint}", params=params)
        out[name] = (resp.status_code, resp.json())
    empty = await create_test_repo(client)
    resp = await client.get(f"/api/graph/{empty['id']}/execution-flows")
    out["flows_empty_repo"] = (resp.status_code, resp.json())
    return out


_EXPECTED = Path(__file__).parents[2] / "fixtures" / "server" / "graph_intelligence_bodies.json"


@pytest.mark.asyncio
async def test_endpoint_bodies_are_pinned(client: AsyncClient, app) -> None:
    got = json.loads(json.dumps(await capture(client, app)))
    expected = json.loads(_EXPECTED.read_text(encoding="utf-8"))
    assert sorted(got) == sorted(expected)
    for name, (status, body) in expected.items():
        assert got[name] == [status, body], name
