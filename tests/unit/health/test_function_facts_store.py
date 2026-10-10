"""The per-function store: facts counted on the complexity walk, roles restamped every run."""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pytest
from sqlalchemy import select

from repowise.core.analysis.execution_roles import ExecutionRoles
from repowise.core.analysis.health import HealthAnalyzer
from repowise.core.analysis.health.complexity.walker import walk_file
from repowise.core.analysis.health.dataflow import FunctionFacts

_PY = b'''
class Store:
    async def save(self, rows):
        if not rows:
            return 0
        await flush()
        self.count = len(rows)
        self.cache[rows[0]] = 1
        return self.count

    def each(self):
        for row in self.rows:
            yield row


def plain(a):
    if a:
        raise ValueError(a)
    return a
'''

_TS = b"""class Counter { bump() { if (this.off) return; this.n++; return this.n; } }
async function load() { await fetch("x"); throw new Error("no"); }
function* ids() { yield 1; }
"""


def _facts(path: str, language: str, source: bytes) -> dict[str, FunctionFacts | None]:
    return {fc.name: fc.facts for fc in walk_file(path, language, source).functions}


def test_the_walk_reads_whole_function_facts_in_python() -> None:
    facts = _facts("store.py", "python", _PY)
    assert facts["save"] == FunctionFacts(
        awaits=True,
        is_generator=False,
        uses_receiver=True,
        receiver_assigns=("cache", "count"),
        early_exits=1,
    )
    assert facts["each"] == FunctionFacts(False, True, True, (), 0)
    assert facts["plain"] == FunctionFacts(False, False, False, (), 1)


def test_the_walk_reads_whole_function_facts_in_typescript() -> None:
    facts = _facts("counter.ts", "typescript", _TS)
    assert facts["bump"] == FunctionFacts(False, False, True, ("n",), 1)
    assert facts["load"] == FunctionFacts(True, False, False, (), 0)
    assert facts["ids"] is not None and facts["ids"].is_generator


class _File:
    def __init__(self, path: str, *, is_test: bool = False) -> None:
        self.file_info = type("FI", (), {"path": path, "is_test": is_test})()


def test_fact_rows_are_keyed_on_the_graph_symbol_and_skip_tests() -> None:
    fcx = walk_file("store.py", "python", _PY)
    graph = nx.DiGraph()
    for fc in fcx.functions:
        graph.add_node(
            f"store.py::{fc.name}",
            node_type="symbol",
            kind="function",
            name=fc.name,
            file_path="store.py",
            start_line=fc.start_line,
            end_line=fc.end_line,
        )
    analyzer = HealthAnalyzer(graph)
    rows = analyzer._function_fact_rows([(_File("store.py"), fcx)])
    assert analyzer._function_fact_rows([(_File("tests/test_store.py", is_test=True), fcx)]) == []
    # No graph: no rows to key, so the writers keep what is stored.
    assert HealthAnalyzer(None)._function_fact_rows([(_File("store.py"), fcx)]) is None
    by_id = {row["symbol_id"]: row for row in rows}
    assert set(by_id) == {"store.py::save", "store.py::each", "store.py::plain"}
    assert by_id["store.py::save"]["receiver_assigns"] == ("cache", "count")
    assert by_id["store.py::save"]["receiver_assigns_known"] is True


@pytest.fixture
async def store(tmp_path: Path):
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )

    engine = create_engine(f"sqlite+aiosqlite:///{(tmp_path / 'wiki.db').as_posix()}")
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="repo", local_path=str(tmp_path))
            await session.commit()
            yield session, repo.id
    finally:
        await engine.dispose()


def _row(symbol: str, **facts) -> dict:
    return {"symbol_id": symbol, **facts}


async def _stored(session, repository_id: str) -> dict[str, tuple]:
    from repowise.core.persistence.models import FunctionFact

    result = await session.execute(
        select(
            FunctionFact.symbol_id,
            FunctionFact.execution_role,
            FunctionFact.awaits,
            FunctionFact.receiver_assigns_known,
            FunctionFact.receiver_assigns_json,
        ).where(FunctionFact.repository_id == repository_id)
    )
    return {row[0]: tuple(row[1:]) for row in result.all()}


async def test_facts_rewrite_walked_files_and_roles_restamp_every_row(store) -> None:
    from repowise.core.persistence.crud import get_function_facts, write_function_facts

    session, repo = store
    roles = ExecutionRoles({"a.py::f": "request", "b.py::g": "cli"})
    written = await write_function_facts(
        session,
        repo,
        [
            _row("a.py::f", awaits=True, receiver_assigns=("n",), receiver_assigns_known=True),
            _row("b.py::g", awaits=False, receiver_assigns=None, receiver_assigns_known=False),
            _row("a.py::h", awaits=False, receiver_assigns=(), receiver_assigns_known=True),
            # A second walked function resolving to the same symbol: the first keeps it.
            _row("b.py::g", awaits=True),
        ],
        roles=roles,
    )
    assert written == 3
    # Unknown and none both store no list; the flag tells them apart.
    assert await _stored(session, repo) == {
        "a.py::f": ("request", True, True, json.dumps(["n"])),
        "a.py::h": ("unknown", False, True, None),
        "b.py::g": ("cli", False, False, None),
    }

    # An update that walked only a.py: b.py keeps its facts, and its role moves
    # because a seed moved elsewhere.
    await write_function_facts(
        session,
        repo,
        [_row("a.py::f", awaits=False, receiver_assigns_known=False)],
        # A Windows path names the same file.
        file_paths={"a.py"},
        roles=ExecutionRoles({"a.py::f": "request", "b.py::g": "request"}),
    )
    assert await _stored(session, repo) == {
        "a.py::f": ("request", False, False, None),
        "b.py::g": ("request", False, False, None),
    }
    # A walked file whose function is gone loses its row.
    await write_function_facts(session, repo, [], file_paths={"a.py"}, roles=None)
    assert set(await get_function_facts(session, repo, ["a.py::f", "b.py::g"])) == {"b.py::g"}


def test_facts_ignore_what_a_lambda_or_nested_function_does() -> None:
    source = b"""
class C:
    async def outer(self):
        def inner():
            yield 1
        f = lambda: self.touch()
        return [x async for x in y]
"""
    facts = _facts("nest.py", "python", source)["outer"]
    assert facts is not None
    assert (facts.is_generator, facts.early_exits) == (False, 0)
    # A lambda shares the instance, so its receiver use counts.
    assert facts.uses_receiver is True


def test_a_windows_path_selects_the_same_rows() -> None:
    from repowise.core.persistence.crud.analysis.function_facts import file_rows

    def compiled(path: str) -> list[str]:
        return [str(c.compile(compile_kwargs={"literal_binds": True})) for c in file_rows("r", path)]

    assert compiled(r"pkg\a.py") == compiled("pkg/a.py")


def test_an_expression_body_is_counted() -> None:
    """``() => expr`` has no statements: the body node itself is what is read."""
    source = b"const f = async () => await g();\nconst h = () => this.x = 1;\n"
    facts = _facts("expr.ts", "typescript", source)
    assert facts["f"] is not None and facts["f"].awaits is True
    assert facts["h"] is not None and facts["h"].receiver_assigns == ("x",)


def test_an_exit_macro_is_an_early_exit() -> None:
    source = b"""fn check(x: i32) -> Result<(), E> {
    if x < 0 { bail!("negative"); }
    if x > 9 { return Err(E); }
    Ok(())
}
fn tail() -> Result<(), E> {
    bail!("always");
}
"""
    facts = _facts("check.rs", "rust", source)
    assert facts["check"] is not None and facts["check"].early_exits == 2
    assert facts["tail"] is not None and facts["tail"].early_exits == 0
