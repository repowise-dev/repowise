"""Answer paths hide graph rows of files excluded after indexing.

The rows for ``archive/`` are written after the index exists, as if the user
added ``archive/**`` to ``exclude_patterns`` later: the stored edges survive,
and every read below must leave them out before its caps.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from repowise.core.exclusion import build_exclude_spec, node_is_excluded
from repowise.core.persistence.models import GraphEdge, GraphNode, Page
from repowise.server.mcp_server import _entry_trace, _flow_path, _wrapper_callers
from repowise.server.mcp_server._answer_pipeline import graph_expand
from repowise.server.mcp_server._helpers import fetch_allowed_edges, filter_edge_rows
from repowise.server.mcp_server.tool_answer import callers, neighbors

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def spec(tmp_path):
    rdir = tmp_path / ".repowise"
    rdir.mkdir(exist_ok=True)
    (rdir / "config.yaml").write_text("exclude_patterns:\n- archive/**\n", encoding="utf-8")
    built = build_exclude_spec(tmp_path)
    assert built is not None
    return built


def _node(rid, node_id, *, kind="function", lines=(10, 30)):
    path, _, name = node_id.partition("::")
    return GraphNode(
        id=f"n:{node_id}",
        repository_id=rid,
        node_id=node_id,
        node_type="symbol",
        kind=kind,
        name=name.split(".")[-1],
        file_path=path,
        language="python",
        start_line=lines[0],
        end_line=lines[1],
        is_test=False,
        pagerank=0.0,
        created_at=_NOW,
    )


def _edge(rid, src, tgt, *, edge_type="calls", confidence=0.9, line=None, **extra):
    return GraphEdge(
        id=f"e:{src}->{tgt}:{edge_type}",
        repository_id=rid,
        source_node_id=src,
        target_node_id=tgt,
        edge_type=edge_type,
        confidence=confidence,
        resolution_origin="import_scoped",
        call_lines_json=json.dumps([line] if line else []),
        created_at=_NOW,
        **extra,
    )


def _page(rid, path):
    return Page(
        id=f"file_page:{path}",
        repository_id=rid,
        page_type="file_page",
        title=f"File: {path}",
        content=f"# {path}",
        summary=f"summary of {path}",
        target_path=path,
        source_hash=path,
        model_name="mock",
        provider_name="mock",
        generation_level=2,
        created_at=_NOW,
        updated_at=_NOW,
    )


# --- shared helpers ---------------------------------------------------------


def test_node_is_excluded_judges_the_file_and_never_external_ids(spec):
    assert node_is_excluded("archive/old.py", spec)
    assert node_is_excluded("archive/old.py::Thing.run", spec)
    assert not node_is_excluded("src/app.py::main", spec)
    assert not node_is_excluded("external:archive/old.py", spec)
    assert not node_is_excluded("archive/old.py", None)
    assert not node_is_excluded(None, spec)


def test_filter_edge_rows_drops_rows_with_any_excluded_end(spec):
    rows = [
        SimpleNamespace(source_node_id="src/a.py", target_node_id="src/b.py"),
        SimpleNamespace(source_node_id="archive/x.py::f", target_node_id="src/b.py"),
        SimpleNamespace(source_node_id="src/a.py", target_node_id="archive/y.py"),
    ]
    assert filter_edge_rows(rows, spec) == rows[:1]
    assert filter_edge_rows(rows, None) == rows
    only_src = filter_edge_rows(rows, spec, endpoints=lambda r: (r.source_node_id,))
    assert only_src == [rows[0], rows[2]]


@pytest.mark.asyncio
async def test_fetch_allowed_edges_fills_the_cap_past_excluded_rows(session, repo_id, spec):
    target = "src/core.py::run"
    session.add_all(
        [_edge(repo_id, f"archive/m{i}.py::f", target, confidence=1.0) for i in range(7)]
        + [_edge(repo_id, f"src/u{i}.py::f", target, confidence=0.8) for i in range(3)]
    )
    await session.flush()
    stmt = (
        select(GraphEdge.source_node_id, GraphEdge.target_node_id)
        .where(GraphEdge.repository_id == repo_id, GraphEdge.target_node_id == target)
        .order_by(GraphEdge.confidence.desc(), GraphEdge.source_node_id, GraphEdge.id)
    )
    kept = await fetch_allowed_edges(session, stmt, spec, limit=3)
    assert [r.source_node_id for r in kept] == [f"src/u{i}.py::f" for i in range(3)]
    raw = await fetch_allowed_edges(session, stmt, None, limit=3)
    assert all(r.source_node_id.startswith("archive/") for r in raw)


@pytest.mark.asyncio
async def test_fetch_allowed_edges_reads_on_past_many_pages_and_can_keep_skipped_rows(
    session, repo_id, spec
):
    target = "src/core.py::run"
    session.add_all(
        [_edge(repo_id, f"archive/m{i:02d}.py::f", target, confidence=1.0) for i in range(40)]
        + [_edge(repo_id, "src/u.py::f", target, confidence=0.5)]
    )
    await session.flush()
    stmt = (
        select(GraphEdge.source_node_id, GraphEdge.target_node_id)
        .where(GraphEdge.repository_id == repo_id, GraphEdge.target_node_id == target)
        .order_by(GraphEdge.confidence.desc(), GraphEdge.source_node_id, GraphEdge.id)
    )
    kept = await fetch_allowed_edges(session, stmt, spec, limit=2)
    assert [r.source_node_id for r in kept] == ["src/u.py::f"]
    with_skipped = await fetch_allowed_edges(session, stmt, spec, limit=2, keep_excluded=True)
    assert len(with_skipped) == 41
    assert with_skipped[-1].source_node_id == "src/u.py::f"


# --- flow path adjacency ----------------------------------------------------


@pytest.mark.asyncio
async def test_file_adjacency_drops_excluded_files_as_keys_and_neighbours(session, repo_id, spec):
    session.add_all(
        [
            _edge(
                repo_id, "src/start.py", "archive/bridge.py", edge_type="imports", confidence=1.0
            ),
            _edge(repo_id, "archive/bridge.py", "src/end.py", edge_type="imports", confidence=1.0),
            _edge(repo_id, "src/start.py", "src/mid.py", edge_type="imports", confidence=1.0),
            _edge(repo_id, "src/mid.py", "src/other.py", edge_type="imports", confidence=1.0),
            _edge(repo_id, "src/other.py", "src/end.py", edge_type="imports", confidence=1.0),
        ]
    )
    await session.flush()

    raw = await _flow_path.file_adjacency(session, repo_id)
    assert "archive/bridge.py" in raw
    assert _flow_path._bfs_path(raw, "src/start.py", "src/end.py", 4) == [
        "src/start.py",
        "archive/bridge.py",
        "src/end.py",
    ]

    adj = await _flow_path.file_adjacency(session, repo_id, spec)
    assert not any(k.startswith("archive/") for k in adj)
    assert not any(n.startswith("archive/") for ns in adj.values() for n in ns)
    path = _flow_path._bfs_path(adj, "src/start.py", "src/end.py", 4)
    assert path == ["src/start.py", "src/mid.py", "src/other.py", "src/end.py"]
    # The persisted copy is still the raw one.
    assert "archive/bridge.py" in await _flow_path.file_adjacency(session, repo_id)


# --- retrieval expansion ----------------------------------------------------


@pytest.mark.asyncio
async def test_graph_expansion_drops_excluded_neighbours_before_the_cap(monkeypatch, factory, spec):
    archived = [f"archive/a{i}.py" for i in range(3)]
    allowed = "src/near.py"
    graph = graph_expand._ProjectedGraph(
        {"src/seed.py": {*archived, allowed}},
        {p: 1 for p in [*archived, allowed, "src/seed.py"]},
    )

    async def fake_graph(_session, _repo_id, _spec=None):
        return graph

    async def fake_pages(_session, neighbours):
        rows = [(p, f"summary of {p}", "file_page") for p in sorted(neighbours)]
        # Excluded files rank first: without the filter they fill the cap of 3.
        return rows, {p: (1.0 if p.startswith("archive/") else 0.1) for p in neighbours}

    monkeypatch.setattr(graph_expand, "projected_graph", fake_graph)
    monkeypatch.setattr(graph_expand, "_neighbor_pages", fake_pages)
    ctx = SimpleNamespace(session_factory=factory)
    hits = [{"target_path": "src/seed.py", "score": 1.0, "page_type": "file_page"}]

    unfiltered = await graph_expand.expand_via_graph(list(hits), ctx, "r")
    assert allowed not in {h["target_path"] for h in unfiltered}

    out = await graph_expand.expand_via_graph(list(hits), ctx, "r", spec)
    paths = {h["target_path"] for h in out}
    assert allowed in paths
    assert not any(p.startswith("archive/") for p in paths)


@pytest.mark.asyncio
async def test_hub_degree_counts_only_edges_between_allowed_files(monkeypatch, spec):
    # src/near.py's edges mostly go to excluded files; they must not make it a hub.
    pairs = [("src/near.py", f"archive/a{i}.py") for i in range(5)] + [("src/seed.py", "src/near.py")]

    async def fake_pairs(_session, _repo_id):
        return list(pairs)

    monkeypatch.setattr(graph_expand, "_projected_edges", fake_pairs)
    raw = await graph_expand._projected_graph(None, "r")
    filtered = await graph_expand._projected_graph(None, "r", spec)
    assert raw.degree["src/near.py"] == 6
    assert filtered.degree["src/near.py"] == 1
    assert not any(p.startswith("archive/") for p in filtered.degree)
    assert filtered.neighbours["src/near.py"] == {"src/seed.py"}


# --- entry trace ------------------------------------------------------------


@pytest.mark.asyncio
async def test_entry_sequence_never_prints_an_excluded_step(session, repo_id, spec):
    entry = "src/cli.py::serve_command"
    archived = [f"archive/old{i}.py::step{i}" for i in range(12)]
    allowed = ["src/cli.py::load_settings", "src/cli.py::start_server"]
    session.add_all([_node(repo_id, n) for n in [entry, *archived, *allowed]])
    edges = [_edge(repo_id, entry, a, line=5 + i) for i, a in enumerate(archived)]
    edges += [_edge(repo_id, entry, a, line=100 + i) for i, a in enumerate(allowed)]
    # Heavy excluded steps: without the filter they win every step slot.
    edges += [_edge(repo_id, a, f"src/lib.py::h{j}", line=1) for a in archived for j in range(3)]
    session.add_all(edges)
    session.add(_page(repo_id, "src/cli.py"))
    await session.flush()

    hits = [{"target_path": "src/cli.py", "score": 1.0, "page_type": "file_page"}]
    question = "What happens step by step when a user runs `serve command`?"
    _raw, raw_paths = await _entry_trace.expand_via_entry_trace(
        session, repo_id, [dict(h) for h in hits], question
    )
    assert any(s.startswith("archive/") for s in raw_paths[0])
    out, paths = await _entry_trace.expand_via_entry_trace(session, repo_id, hits, question, spec)
    assert paths == [[entry, *allowed]]
    seq = next(h for h in out if h["target_path"] == "src/cli.py")["_call_sequence"]
    assert "archive/" not in seq
    assert all("archive/" not in h["target_path"] for h in out)


@pytest.mark.asyncio
async def test_call_route_and_call_path_never_start_in_an_excluded_file(session, repo_id, spec):
    start = "archive/old.py::go"
    mid = "src/mid.py::relay"
    end = "src/end.py::finish"
    session.add_all([_node(repo_id, n) for n in (start, mid, end)])
    session.add_all([_edge(repo_id, start, mid, line=3), _edge(repo_id, mid, end, line=4)])
    await session.flush()

    assert await _entry_trace.call_route(session, repo_id, [start], [end]) != []
    assert await _entry_trace.call_route(session, repo_id, [start], [end], spec) == []
    assert await _entry_trace.call_path(session, repo_id, start, spec) == []
    into = await _entry_trace.call_path(session, repo_id, mid, spec, upstream=True)
    assert into == []


# --- answer callers ---------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_callers_fill_the_cap_past_excluded_callers(session, repo_id, spec):
    target = _node(repo_id, "src/core.py::handle")
    archived = [f"archive/c{i:02d}.py::call" for i in range(callers._MAX_DIRECT)]
    allowed = ["src/a.py::use", "src/b.py::use"]
    session.add(target)
    session.add_all([_edge(repo_id, a, target.node_id, confidence=1.0) for a in archived])
    session.add_all([_edge(repo_id, a, target.node_id, confidence=0.8) for a in allowed])
    await session.flush()

    raw = await callers._direct_edges(session, repo_id, target, cross_file=False)
    assert all(e.source_node_id.startswith("archive/") for e in raw)
    kept = await callers._direct_edges(
        session, repo_id, target, cross_file=False, exclude_spec=spec
    )
    assert [e.source_node_id for e in kept] == allowed


@pytest.mark.asyncio
async def test_named_targets_skip_definitions_in_excluded_files(session, repo_id, spec):
    session.add_all(
        [_node(repo_id, "src/core.py::dispatch"), _node(repo_id, "archive/core.py::dispatch")]
    )
    await session.flush()
    found = await callers._named_targets(session, repo_id, ["dispatch"], "", spec)
    assert [n.node_id for n in found] == ["src/core.py::dispatch"]


@pytest.mark.asyncio
async def test_excluded_caller_does_not_suppress_registration_evidence(session, repo_id, spec):
    handler = _node(repo_id, "src/handlers.py::on_event")
    session.add_all([handler, _node(repo_id, "archive/runner.py::run")])
    session.add_all(
        [
            _edge(repo_id, "archive/runner.py::run", handler.node_id),
            _edge(
                repo_id,
                "src/registry.py",
                "src/handlers.py",
                edge_type="imports",
                confidence=1.0,
                imported_names_json=json.dumps(["on_event"]),
            ),
        ]
    )
    await session.flush()
    rows = [callers.caller_row(handler.node_id, "src/handlers.py", 10, "src/x.py::t", "calls")]
    nodes = {handler.node_id: handler}

    assert await callers._registering_files(session, repo_id, rows, nodes) == []
    found = await callers._registering_files(session, repo_id, rows, nodes, spec)
    assert [r["file"] for r in found] == ["src/registry.py"]


# --- neighbours -------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbound_symbol_evidence_fills_the_cap_past_excluded_sources(session, repo_id, spec):
    target = "src/lead.py::work"
    session.add_all(
        [_edge(repo_id, f"archive/s{i}.py::f", target, confidence=1.0) for i in range(4)]
    )
    session.add_all([_edge(repo_id, f"src/s{i}.py::f", target, confidence=0.8) for i in range(2)])
    await session.flush()

    kept = await neighbors._inbound(session, repo_id, [target], 2, spec)
    assert [e.source_node_id for e in kept] == ["src/s0.py::f", "src/s1.py::f"]


@pytest.mark.asyncio
async def test_neighbor_evidence_for_an_excluded_lead_is_empty(session, repo_id, spec):
    session.add(_node(repo_id, "archive/lead.py::work"))
    session.add(_edge(repo_id, "src/u.py::f", "archive/lead.py::work"))
    await session.flush()
    assert await neighbors.neighbor_evidence(session, repo_id, "archive/lead.py", [], spec) == []


@pytest.mark.asyncio
async def test_neighbor_rows_carry_no_excluded_wrapper_or_caller(session, repo_id, spec):
    lead = "src/lead.py::work"
    shim = "archive/shim.py::work"
    session.add_all([_node(repo_id, lead), _node(repo_id, shim, lines=(1, 3))])
    session.add_all(
        [
            _edge(repo_id, shim, lead),
            _edge(repo_id, "src/user.py::main", shim),
            _edge(repo_id, "src/direct.py::f", lead),
        ]
    )
    await session.flush()
    rows = await neighbors.neighbor_evidence(session, repo_id, "src/lead.py", [], spec)
    assert [r["file"] for r in rows] == ["src/direct.py"]
    assert not any("via_wrapper" in r for r in rows)


# --- forwarding wrappers ----------------------------------------------------


@pytest.mark.asyncio
async def test_wrapper_in_an_excluded_file_is_never_followed(session, repo_id, spec):
    target = _node(repo_id, "src/core.py::run")
    shim = _node(repo_id, "archive/shim.py::run", lines=(1, 3))
    session.add_all([target, shim])
    session.add_all(
        [
            _edge(repo_id, shim.node_id, target.node_id),
            _edge(repo_id, "src/use.py::go", shim.node_id),
        ]
    )
    await session.flush()

    raw = await _wrapper_callers.forwarding_wrapper_callers(
        session, repo_id, target, [shim.node_id]
    )
    assert [r["via_wrapper"] for r in raw] == [shim.node_id]
    kept = await _wrapper_callers.forwarding_wrapper_callers(
        session, repo_id, target, [shim.node_id], exclude_spec=spec
    )
    assert kept == []


@pytest.mark.asyncio
async def test_excluded_wrapper_callers_neither_fill_the_cap_nor_block_the_fallback(
    session, repo_id, spec
):
    target = _node(repo_id, "src/core.py::run")
    wrapper = _node(repo_id, "src/shim.py::run", lines=(1, 3))
    session.add_all([target, wrapper])
    session.add_all(
        [
            _edge(repo_id, wrapper.node_id, target.node_id),
            # The only call into the wrapper sits in an excluded file.
            _edge(repo_id, "archive/old.py::go", wrapper.node_id),
            _edge(
                repo_id,
                "archive/loader.py",
                "src/shim.py",
                edge_type="imports",
                confidence=1.0,
                imported_names_json=json.dumps(["run"]),
            ),
            _edge(
                repo_id,
                "src/loader.py",
                "src/shim.py",
                edge_type="imports",
                confidence=1.0,
                imported_names_json=json.dumps(["run"]),
            ),
        ]
    )
    await session.flush()

    raw = await _wrapper_callers.forwarding_wrapper_callers(
        session, repo_id, target, [wrapper.node_id]
    )
    assert [r.get("symbol_id") for r in raw] == ["archive/old.py::go"]
    rows = await _wrapper_callers.forwarding_wrapper_callers(
        session, repo_id, target, [wrapper.node_id], limit=1, exclude_spec=spec
    )
    assert [r["file"] for r in rows] == ["src/loader.py"]
    assert rows[0]["via_wrapper"] == wrapper.node_id
