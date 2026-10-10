"""Callers one hop past a forwarding wrapper (``_wrapper_callers``).

Synthetic graphs in three languages: the hop is added and marked for a real
wrapper, and withheld for a caller that does other work.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from repowise.core.persistence.models import GraphEdge, GraphNode, Repository
from repowise.server.mcp_server import _basis, _wrapper_callers
from repowise.server.mcp_server.tool_context import enrichment

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fresh_basis_cache():
    _basis.reset_cache()
    yield
    _basis.reset_cache()


def _sym(rid, node_id, *, lines=(10, 30), kind="function", language="typescript", name=None):
    file_path, _, qual = node_id.partition("::")
    return GraphNode(
        id=f"w_{node_id}",
        repository_id=rid,
        node_id=node_id,
        node_type="symbol",
        name=name or qual.split(".")[-1],
        file_path=file_path,
        kind=kind,
        language=language,
        start_line=lines[0],
        end_line=lines[1],
        created_at=_NOW,
    )


def _edge(rid, source, target, *, edge_type="calls", confidence=0.9, **extra):
    return GraphEdge(
        id=f"w_{source}->{target}:{edge_type}",
        repository_id=rid,
        source_node_id=source,
        target_node_id=target,
        edge_type=edge_type,
        confidence=confidence,
        created_at=_NOW,
        **extra,
    )


async def _callers(session, rid, target, **kw) -> dict:
    out: dict = {}
    await enrichment._resolve_call_graph(
        session,
        await session.get(Repository, rid),
        target,
        "symbol",
        out,
        want_callers=True,
        **kw,
    )
    return out


@pytest.mark.asyncio
async def test_ts_same_named_runtime_wrapper_lists_its_callers_marked(session, populated_db):
    rid = populated_db
    session.add_all(
        [
            _sym(rid, "src/poll.ts::prune", lines=(59, 70)),
            _sym(rid, "src/poll.runtime.ts::prune", lines=(9, 13)),
            _sym(rid, "src/diag.ts::tick", lines=(100, 140)),
            _sym(rid, "src/poll.test.ts::__module__", lines=(1, 200), kind="module"),
            _edge(rid, "src/poll.runtime.ts::prune", "src/poll.ts::prune"),
            _edge(rid, "src/poll.test.ts::__module__", "src/poll.ts::prune"),
            _edge(rid, "src/diag.ts::tick", "src/poll.runtime.ts::prune", resolution_origin="import_scoped"),
        ]
    )
    await session.flush()

    out = await _callers(session, rid, "src/poll.ts::prune")
    ids = [r.get("symbol_id") for r in out["callers"]]
    assert set(ids[:2]) == {"src/poll.runtime.ts::prune", "src/poll.test.ts::__module__"}
    hop = out["callers"][2]
    assert hop["symbol_id"] == "src/diag.ts::tick"
    assert hop["file"] == "src/diag.ts"
    assert hop["edge_type"] == "calls"
    assert hop["via"] == "import_scoped"
    assert hop["via_wrapper"] == "src/poll.runtime.ts::prune"
    assert all("via_wrapper" not in r for r in out["callers"][:2])
    # Direct-caller counts are untouched by the hop rows.
    assert "callers_total" not in out


@pytest.mark.asyncio
async def test_python_pass_through_with_another_name_is_a_wrapper(session, populated_db):
    rid = populated_db
    session.add_all(
        [
            _sym(rid, "pkg/core.py::_load_impl", lines=(5, 40), language="python"),
            _sym(rid, "pkg/api.py::load", lines=(12, 14), language="python"),
            _sym(rid, "app/main.py::run", lines=(3, 20), language="python"),
            _edge(rid, "pkg/api.py::load", "pkg/core.py::_load_impl"),
            _edge(rid, "app/main.py::run", "pkg/api.py::load"),
        ]
    )
    await session.flush()

    out = await _callers(session, rid, "pkg/core.py::_load_impl")
    assert [(r["symbol_id"], r.get("via_wrapper")) for r in out["callers"]] == [
        ("pkg/api.py::load", None),
        ("app/main.py::run", "pkg/api.py::load"),
    ]


@pytest.mark.asyncio
async def test_go_caller_doing_other_work_is_not_expanded(session, populated_db):
    """Short but calls something else too, or same-named but long: neither is a wrapper."""
    rid = populated_db
    go = {"language": "go"}
    session.add_all(
        [
            _sym(rid, "store/db.go::Open", lines=(10, 50), **go),
            _sym(rid, "cmd/main.go::handle", lines=(5, 7), **go),
            _sym(rid, "cmd/main.go::logf", lines=(20, 22), **go),
            _sym(rid, "srv/db.go::Open", lines=(10, 60), kind="method", **go),
            _sym(rid, "cmd/run.go::serve", lines=(1, 9), **go),
            _edge(rid, "cmd/main.go::handle", "store/db.go::Open"),
            _edge(rid, "cmd/main.go::handle", "cmd/main.go::logf"),
            _edge(rid, "srv/db.go::Open", "store/db.go::Open"),
            _edge(rid, "cmd/run.go::serve", "cmd/main.go::handle"),
            _edge(rid, "cmd/run.go::serve", "srv/db.go::Open"),
        ]
    )
    await session.flush()

    out = await _callers(session, rid, "store/db.go::Open")
    assert {r["symbol_id"] for r in out["callers"]} == {"cmd/main.go::handle", "srv/db.go::Open"}


@pytest.mark.asyncio
async def test_go_method_forwarder_is_expanded(session, populated_db):
    rid = populated_db
    go = {"language": "go"}
    session.add_all(
        [
            _sym(rid, "store/db.go::Open", lines=(10, 50), **go),
            _sym(rid, "store/client.go::Client.Open", lines=(30, 32), kind="method", **go),
            _sym(rid, "cmd/main.go::main", lines=(1, 30), **go),
            _edge(rid, "store/client.go::Client.Open", "store/db.go::Open"),
            _edge(rid, "cmd/main.go::main", "store/client.go::Client.Open"),
        ]
    )
    await session.flush()

    out = await _callers(session, rid, "store/db.go::Open")
    assert out["callers"][-1]["symbol_id"] == "cmd/main.go::main"
    assert out["callers"][-1]["via_wrapper"] == "store/client.go::Client.Open"


@pytest.mark.asyncio
async def test_wrapper_cycle_adds_nothing_already_listed(session, populated_db):
    """A forwarder the target calls back: its caller is the target, which is never listed."""
    rid = populated_db
    session.add_all(
        [
            _sym(rid, "a.ts::run", lines=(1, 30)),
            _sym(rid, "b.ts::run", lines=(1, 4)),
            _edge(rid, "b.ts::run", "a.ts::run"),
            _edge(rid, "a.ts::run", "b.ts::run"),
        ]
    )
    await session.flush()

    out = await _callers(session, rid, "a.ts::run")
    assert [r["symbol_id"] for r in out["callers"]] == ["b.ts::run"]
    assert all("via_wrapper" not in r for r in out["callers"])


@pytest.mark.asyncio
async def test_same_named_caller_that_calls_more_is_not_a_wrapper(session, populated_db):
    """Same name, other file, short: still not a wrapper once it resolves another call."""
    rid = populated_db
    session.add_all(
        [
            _sym(rid, "lib/impl.ts::save", lines=(1, 40)),
            _sym(rid, "app/model.ts::save", lines=(10, 18)),
            _sym(rid, "app/model.ts::validate", lines=(30, 40)),
            _sym(rid, "app/form.ts::submit", lines=(1, 20)),
            _edge(rid, "app/model.ts::save", "lib/impl.ts::save"),
            _edge(rid, "app/model.ts::save", "app/model.ts::validate"),
            _edge(rid, "app/form.ts::submit", "app/model.ts::save"),
        ]
    )
    await session.flush()

    out = await _callers(session, rid, "lib/impl.ts::save")
    assert [r["symbol_id"] for r in out["callers"]] == ["app/model.ts::save"]


@pytest.mark.asyncio
async def test_hop_rows_are_capped(session, populated_db):
    rid = populated_db
    rows = [
        _sym(rid, "lib/impl.py::work", lines=(1, 40), language="python"),
        _sym(rid, "lib/__init__.py::work", lines=(1, 2), language="python"),
        _edge(rid, "lib/__init__.py::work", "lib/impl.py::work"),
    ]
    for i in range(15):
        rows.append(_sym(rid, f"app/m{i:02}.py::use", lines=(1, 9), language="python"))
        rows.append(_edge(rid, f"app/m{i:02}.py::use", "lib/__init__.py::work"))
    session.add_all(rows)
    await session.flush()

    out = await _callers(session, rid, "lib/impl.py::work")
    hop = [r for r in out["callers"] if "via_wrapper" in r]
    assert len(hop) == _wrapper_callers.MAX_WRAPPER_CALLER_ROWS
    assert [r["symbol_id"] for r in hop] == [f"app/m{i:02}.py::use" for i in range(10)]

    target = await session.get(GraphNode, "w_lib/impl.py::work")
    capped = await _wrapper_callers.forwarding_wrapper_callers(
        session, rid, target, ["lib/__init__.py::work"], limit=3
    )
    assert len(capped) == 3


@pytest.mark.asyncio
async def test_unreached_wrapper_falls_back_to_files_importing_it(session, populated_db):
    """A dynamic import consumed in a callback leaves no call edge; the import edge names the file."""
    rid = populated_db
    session.add_all(
        [
            _sym(rid, "src/poll.ts::prune", lines=(59, 70)),
            _sym(rid, "src/poll.runtime.ts::prune", lines=(9, 13)),
            _edge(rid, "src/poll.runtime.ts::prune", "src/poll.ts::prune"),
            _edge(rid, "src/diag.ts", "src/poll.runtime.ts", edge_type="imports", confidence=1.0,
                  imported_names_json='["*"]'),
            _edge(rid, "src/other.ts", "src/poll.runtime.ts", edge_type="imports", confidence=1.0,
                  imported_names_json='["unrelated"]'),
            _edge(rid, "src/cli.ts", "src/poll.runtime.ts", edge_type="imports", confidence=1.0,
                  imported_names_json='["prune"]'),
        ]
    )
    await session.flush()

    out = await _callers(session, rid, "src/poll.ts::prune")
    named = out["callers"][1]
    assert named["file"] == "src/cli.ts" and "wholesale" not in named
    assert out["callers"][-1] == {
        "file": "src/diag.ts",
        "imports": True,
        "confidence": 1.0,
        "wholesale": True,
        "via_wrapper": "src/poll.runtime.ts::prune",
    }
    assert len(out["callers"]) == 3


@pytest.mark.asyncio
async def test_output_is_byte_identical_when_no_caller_is_a_wrapper(
    session, populated_db, monkeypatch
):
    rid = populated_db
    session.add_all(
        [
            _sym(rid, "x.py::target", lines=(1, 30), language="python"),
            _sym(rid, "y.py::big", lines=(1, 80), language="python"),
            _sym(rid, "z.py::top", lines=(1, 5), language="python"),
            _edge(rid, "y.py::big", "x.py::target"),
            _edge(rid, "z.py::top", "y.py::big"),
        ]
    )
    await session.flush()

    with_hop = await _callers(session, rid, "x.py::target")

    async def _none(*_a, **_k):
        return []

    monkeypatch.setattr(enrichment, "forwarding_wrapper_callers", _none)
    without = await _callers(session, rid, "x.py::target")
    assert json.dumps(with_hop, sort_keys=False) == json.dumps(without, sort_keys=False)
