"""Caller rows carry the live source line at the call (``call_line`` + ``text``).

Synthetic graphs in TypeScript, Python and Go over files written to the
repository root: get_context caller rows (direct and wrapper hop) and
get_answer ``graph_callers`` rows read the edge's first call line live.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from repowise.core.persistence.models import GraphEdge, GraphNode, Repository
from repowise.server.mcp_server import _basis
from repowise.server.mcp_server._edit_sites import MAX_TEXT_CHARS
from repowise.server.mcp_server.tool_answer import callers
from repowise.server.mcp_server.tool_context import enrichment

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fresh_basis_cache():
    _basis.reset_cache()
    yield
    _basis.reset_cache()


def _sym(rid, node_id, *, lines=(1, 30), kind="function", language="typescript"):
    file_path, _, qual = node_id.partition("::")
    return GraphNode(
        id=f"ct_{node_id}",
        repository_id=rid,
        node_id=node_id,
        node_type="symbol",
        name=qual.split(".")[-1],
        file_path=file_path,
        kind=kind,
        language=language,
        start_line=lines[0],
        end_line=lines[1],
        created_at=_NOW,
    )


def _edge(rid, source, target, *, call_lines=None, edge_type="calls", names=None):
    return GraphEdge(
        id=f"ct_{source}->{target}:{edge_type}",
        repository_id=rid,
        source_node_id=source,
        target_node_id=target,
        edge_type=edge_type,
        confidence=0.9,
        imported_names_json=json.dumps(names or []),
        call_lines_json=json.dumps(call_lines or []),
        created_at=_NOW,
    )


def _write(root, rel: str, lines: list[str]) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _callers(session, rid, target, root, **kw) -> list[dict]:
    out: dict = {}
    await enrichment._resolve_call_graph(
        session,
        await session.get(Repository, rid),
        target,
        "symbol",
        out,
        want_callers=True,
        repo_root=root,
        **kw,
    )
    return out["callers"]


@pytest.mark.asyncio
async def test_ts_direct_caller_row_carries_its_call_line(session, populated_db, tmp_path):
    rid = populated_db
    _write(tmp_path, "src/diag.ts", ["export function tick() {", "  const n = 1;",
                                     "    prune(n, Date.now());   ", "}"])
    session.add_all(
        [
            _sym(rid, "src/poll.ts::prune", lines=(10, 40)),
            _sym(rid, "src/diag.ts::tick", lines=(1, 4)),
            _edge(rid, "src/diag.ts::tick", "src/poll.ts::prune", call_lines=[3]),
        ]
    )
    await session.flush()

    (row,) = await _callers(session, rid, "src/poll.ts::prune", tmp_path)
    assert row["line"] == 1
    assert row["call_line"] == 3
    assert row["text"] == "prune(n, Date.now());"


@pytest.mark.asyncio
async def test_python_wrapper_hop_row_carries_its_call_line(session, populated_db, tmp_path):
    rid = populated_db
    py = {"language": "python"}
    _write(tmp_path, "pkg/api.py", ["def load(path):", "    return _load_impl(path)"])
    _write(tmp_path, "app/main.py", ["def run():", "    cfg = load('x.toml')", "    return cfg"])
    session.add_all(
        [
            _sym(rid, "pkg/core.py::_load_impl", lines=(5, 40), **py),
            _sym(rid, "pkg/api.py::load", lines=(1, 2), **py),
            _sym(rid, "app/main.py::run", lines=(1, 3), **py),
            _edge(rid, "pkg/api.py::load", "pkg/core.py::_load_impl", call_lines=[2]),
            _edge(rid, "app/main.py::run", "pkg/api.py::load", call_lines=[2]),
        ]
    )
    await session.flush()

    direct, hop = await _callers(session, rid, "pkg/core.py::_load_impl", tmp_path)
    assert direct["text"] == "return _load_impl(path)"
    assert hop["via_wrapper"] == "pkg/api.py::load"
    assert (hop["call_line"], hop["text"]) == (2, "cfg = load('x.toml')")


@pytest.mark.asyncio
async def test_go_several_call_lines_serve_the_first(session, populated_db, tmp_path):
    rid = populated_db
    go = {"language": "go"}
    _write(tmp_path, "cmd/main.go", ["func main() {", "\tdb := store.Open(a)", "\tlog(db)",
                                     "\tstore.Open(b)", "}"])
    session.add_all(
        [
            _sym(rid, "store/db.go::Open", lines=(10, 50), **go),
            _sym(rid, "cmd/main.go::main", lines=(1, 5), **go),
            _edge(rid, "cmd/main.go::main", "store/db.go::Open", call_lines=[4, 2]),
        ]
    )
    await session.flush()

    (row,) = await _callers(session, rid, "store/db.go::Open", tmp_path)
    assert (row["call_line"], row["text"]) == (2, "db := store.Open(a)")


@pytest.mark.asyncio
async def test_missing_file_short_file_and_unlined_edge_get_no_text(
    session, populated_db, tmp_path
):
    rid = populated_db
    _write(tmp_path, "src/short.ts", ["run();"])
    _write(tmp_path, "src/long.ts", ["  run(" + "x" * 400 + ");"])
    session.add_all(
        [
            _sym(rid, "src/lib.ts::run", lines=(1, 20)),
            _sym(rid, "src/gone.ts::a", lines=(1, 9)),
            _sym(rid, "src/short.ts::b", lines=(1, 9)),
            _sym(rid, "src/plain.ts::c", lines=(1, 9)),
            _sym(rid, "src/long.ts::d", lines=(1, 9)),
            _edge(rid, "src/gone.ts::a", "src/lib.ts::run", call_lines=[3]),
            _edge(rid, "src/short.ts::b", "src/lib.ts::run", call_lines=[7]),
            _edge(rid, "src/plain.ts::c", "src/lib.ts::run"),
            _edge(rid, "src/long.ts::d", "src/lib.ts::run", call_lines=[1]),
        ]
    )
    await session.flush()

    rows = {r["file"]: r for r in await _callers(session, rid, "src/lib.ts::run", tmp_path)}
    # An unchecked line is not served, so the row keeps only its definition line.
    assert "call_line" not in rows["src/gone.ts"] and "text" not in rows["src/gone.ts"]
    assert "call_line" not in rows["src/short.ts"] and "text" not in rows["src/short.ts"]
    assert rows["src/gone.ts"]["line"] == 1
    assert "call_line" not in rows["src/plain.ts"] and "text" not in rows["src/plain.ts"]
    assert len(rows["src/long.ts"]["text"]) == MAX_TEXT_CHARS
    assert rows["src/long.ts"]["text"].startswith("run(xxx")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "language, caller_file, lines",
    [
        # An edit above the call moved it down two lines since indexing.
        ("typescript", "src/diag.ts",
         ["export function tick() {", "  const a = 1;", "  const b = 2;", "  prune(a, b);", "}"]),
        ("python", "app/diag.py",
         ["def tick():", "    a = 1", "    b = 2", "    prune(a, b)"]),
    ],
)
async def test_shifted_call_line_is_dropped(
    session, populated_db, tmp_path, language, caller_file, lines
):
    rid = populated_db
    ext = caller_file.rsplit(".", 1)[1]
    _write(tmp_path, caller_file, lines)
    session.add_all(
        [
            _sym(rid, f"lib/poll.{ext}::prune", lines=(10, 40), language=language),
            _sym(rid, f"{caller_file}::tick", lines=(1, 5), language=language),
            _edge(rid, f"{caller_file}::tick", f"lib/poll.{ext}::prune", call_lines=[2]),
        ]
    )
    await session.flush()

    (row,) = await _callers(session, rid, f"lib/poll.{ext}::prune", tmp_path)
    assert row["line"] == 1
    assert "call_line" not in row and "text" not in row


@pytest.mark.asyncio
async def test_call_through_an_import_alias_is_served(session, populated_db, tmp_path):
    rid = populated_db
    _write(tmp_path, "src/poll.runtime.ts", [
        'import { prune as pruneImpl } from "./poll.js";',
        "export function prune(...args) {",
        "  return pruneImpl(...args);",
        "}",
    ])
    session.add_all(
        [
            _sym(rid, "src/poll.ts::prune", lines=(10, 40)),
            _sym(rid, "src/poll.runtime.ts::prune", lines=(2, 4)),
            _edge(rid, "src/poll.runtime.ts::prune", "src/poll.ts::prune", call_lines=[3]),
        ]
    )
    await session.flush()

    (row,) = await _callers(session, rid, "src/poll.ts::prune", tmp_path)
    assert (row["call_line"], row["text"]) == (3, "return pruneImpl(...args);")


@pytest.mark.asyncio
async def test_get_context_without_callers_is_byte_identical(
    setup_mcp, factory, tmp_path, monkeypatch
):
    from repowise.server.mcp_server import get_context

    rid = setup_mcp
    _write(tmp_path, "src/diag.ts", ["export function tick() {", "  prune();", "}"])
    async with factory() as s:
        s.add_all(
            [
                _sym(rid, "src/poll.ts::prune", lines=(10, 40)),
                _sym(rid, "src/diag.ts::tick", lines=(1, 3)),
                _edge(rid, "src/diag.ts::tick", "src/poll.ts::prune", call_lines=[2]),
            ]
        )
        await s.commit()

    def _strip(result: dict) -> str:
        return json.dumps({k: v for k, v in result.items() if k != "_meta"}, sort_keys=True)

    with_callers = await get_context(targets=["src/poll.ts::prune"], include=["callers"])
    assert '"text": "prune();"' in json.dumps(with_callers)
    shown = {
        str(include): await get_context(targets=["src/diag.ts::tick"], include=include)
        for include in (None, ["callees"])
    }

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(enrichment, "attach_call_text", _noop)
    monkeypatch.setattr(enrichment, "first_call_line", lambda _raw: None)
    for include in (None, ["callees"]):
        without = await get_context(targets=["src/diag.ts::tick"], include=include)
        assert _strip(shown[str(include)]) == _strip(without)


@pytest.mark.asyncio
async def test_graph_callers_rows_carry_call_text(session, populated_db, tmp_path):
    rid = populated_db
    _write(tmp_path, "src/api/login.ts", ["export async function handleLogin(req) {",
                                          "  const ok = await verifyToken(req.token);", "}"])
    _write(tmp_path, "src/auth/tokens.runtime.ts", ["export function checkToken(t) {",
                                                    "  return impl.verifyToken(t);", "}"])
    _write(tmp_path, "src/jobs/sweep.ts", ["function sweep() {", "", "  checkToken(t);", "}"])
    session.add_all(
        [
            _sym(rid, "src/auth/tokens.ts::verifyToken", lines=(12, 40)),
            _sym(rid, "src/api/login.ts::handleLogin", lines=(1, 3)),
            _sym(rid, "src/auth/tokens.runtime.ts::checkToken", lines=(1, 3)),
            _sym(rid, "src/jobs/sweep.ts::sweep", lines=(1, 4)),
            _edge(rid, "src/api/login.ts::handleLogin", "src/auth/tokens.ts::verifyToken",
                  call_lines=[2]),
            _edge(rid, "src/auth/tokens.runtime.ts::checkToken",
                  "src/auth/tokens.ts::verifyToken", call_lines=[2]),
            _edge(rid, "src/jobs/sweep.ts::sweep", "src/auth/tokens.runtime.ts::checkToken",
                  call_lines=[3]),
            _edge(rid, "src/cli.ts", "src/api/login.ts", edge_type="imports",
                  names=["handleLogin"]),
        ]
    )
    await session.flush()

    ev = await callers.caller_evidence(
        session, rid, "Who calls verifyToken?", {"verifyToken"}, [], repo_root=tmp_path
    )
    rows = {r["caller"]: r for r in ev.rows}
    login = rows["src/api/login.ts::handleLogin"]
    assert (login["line"], login["call_line"]) == (1, 2)
    assert login["text"] == "const ok = await verifyToken(req.token);"
    hop = rows["src/jobs/sweep.ts::sweep"]
    assert hop["via_wrapper"] == "src/auth/tokens.runtime.ts::checkToken"
    assert (hop["call_line"], hop["text"]) == (3, "checkToken(t);")
    # An importer row names no line, so it carries no text.
    importer = rows["src/cli.ts"]
    assert "call_line" not in importer and "text" not in importer

    unrooted = await callers.caller_evidence(
        session, rid, "Who calls verifyToken?", {"verifyToken"}, []
    )
    assert all("text" not in r and "call_line" not in r for r in unrooted.rows)
    assert "handleLogin (src/api/login.ts:1) calls" in callers.caller_sentence(unrooted.rows)


@pytest.mark.asyncio
async def test_graph_callers_drop_a_shifted_call_line(session, populated_db, tmp_path):
    rid = populated_db
    py = {"language": "python"}
    _write(tmp_path, "app/views.py", ["def login(req):", "    log(req)", "", "    check_token(req)"])
    session.add_all(
        [
            _sym(rid, "auth/core.py::check_token", lines=(3, 20), **py),
            _sym(rid, "app/views.py::login", lines=(1, 4), **py),
            _edge(rid, "app/views.py::login", "auth/core.py::check_token", call_lines=[2]),
        ]
    )
    await session.flush()

    ev = await callers.caller_evidence(
        session, rid, "Who calls check_token?", {"check_token"}, [], repo_root=tmp_path
    )
    (row,) = ev.rows
    assert "call_line" not in row and "text" not in row
    assert callers.caller_sentence(ev.rows).startswith(
        "From the call graph: login (app/views.py:1) calls check_token"
    )


@pytest.mark.asyncio
async def test_get_answer_serves_call_text_on_graph_callers(setup_mcp, factory, tmp_path, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    from .test_answer_graph_callers import _patch_retrieval

    rid = setup_mcp
    _write(tmp_path, "src/api/login.ts", ["export async function handleLogin(req) {",
                                          "  return verifyToken(req.token);", "}"])
    async with factory() as s:
        s.add_all(
            [
                _sym(rid, "src/auth/tokens.ts::verifyToken", lines=(12, 40)),
                _sym(rid, "src/api/login.ts::handleLogin", lines=(1, 3)),
                _edge(rid, "src/api/login.ts::handleLogin", "src/auth/tokens.ts::verifyToken",
                      call_lines=[2]),
            ]
        )
        await s.commit()
    _patch_retrieval(monkeypatch, answer_mod)
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: None)

    result = await get_answer("Which non-test files call verifyToken?")
    (row,) = result["graph_callers"]
    assert (row["line"], row["call_line"]) == (1, 2)
    assert row["text"] == "return verifyToken(req.token);"
    # The keyless lead cites the call, not the caller's definition line.
    assert result["answer"].startswith(
        "From the call graph: handleLogin (src/api/login.ts:2) calls verifyToken"
    )
