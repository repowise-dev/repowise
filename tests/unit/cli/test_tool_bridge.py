"""The CLI's wiring of MCP tool state.

The tools read their session factory, full-text index and vector store out of
``mcp_server._state``, which the MCP server's lifespan normally publishes.
This bridge is the CLI's caller of the same publisher, and the two things that
can go wrong are silent: publishing the wrong objects (the tool answers from
another repo's database) and not tearing down (a leaked engine per invocation,
invisible in a one-shot process and fatal in a test suite).
"""

from __future__ import annotations

import pytest

from repowise.cli import tool_bridge


class _FakeEngine:
    def __init__(self) -> None:
        self.disposed = False
        self.reconciled = False

    async def dispose(self) -> None:
        self.disposed = True


class _FakeStore:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Patch the bridge's collaborators and record what it published."""
    engine = _FakeEngine()
    store = _FakeStore()
    published: dict = {}

    monkeypatch.setattr(
        "repowise.core.persistence.create_engine", lambda url: engine, raising=False
    )

    async def _fake_reconcile(eng):
        engine.reconciled = True

    monkeypatch.setattr("repowise.cli.helpers.reconcile_schema_best_effort", _fake_reconcile)
    monkeypatch.setattr(
        "repowise.core.persistence.FullTextSearch", lambda eng: ("fts", eng), raising=False
    )
    monkeypatch.setattr(
        "repowise.cli.helpers.get_db_url_for_repo", lambda p: f"sqlite+aiosqlite:///{p}"
    )

    async def _fake_open(repo_path):
        return store

    monkeypatch.setattr(tool_bridge, "_open_vector_store", _fake_open)

    def _fake_init(session_factory, fts, vector_store, decision_store=None, repo_path=None):
        published.update(
            session_factory=session_factory,
            fts=fts,
            vector_store=vector_store,
            decision_store=decision_store,
            repo_path=repo_path,
        )

    monkeypatch.setattr("repowise.server.mcp_server._state.init_tool_state", _fake_init)

    async def _no_identity(session_factory):
        return None

    monkeypatch.setattr("repowise.server.mcp_server._state.publish_served_repo_id", _no_identity)
    return engine, store, published, tmp_path


def _answer_only(result: dict) -> dict:
    """The tool's own payload, minus the budget envelope the bridge stamps.

    A bridged call is budgeted the way the MCP middleware budgets an MCP call,
    so every response gains ``_meta.response_budget`` and ``_meta.completeness``.
    What must not change is the answer the tool returned.
    """
    stamped = {"response_budget", "completeness"}
    meta = {k: v for k, v in (result.get("_meta") or {}).items() if k not in stamped}
    answer = {k: v for k, v in result.items() if k != "_meta"}
    if meta:
        answer["_meta"] = meta
    return answer


def test_it_publishes_this_repos_resources_and_tears_them_down(wired):
    engine, store, published, repo = wired

    async def _tool():
        return {"ok": True}

    assert _answer_only(tool_bridge.call_tool(repo, _tool, "get_symbol")) == {"ok": True}
    # The session factory is the object that decides *which repo's database*
    # answers, so it is the one most worth pinning.
    assert published["session_factory"].kw["bind"] is engine
    assert published["vector_store"] is store
    # The server points both at one store: decisions share the page table under
    # a "decision:" namespace rather than living in a separate one.
    assert published["decision_store"] is store
    assert published["repo_path"] == str(repo)
    assert published["fts"] == ("fts", engine)
    assert engine.disposed and store.closed


def test_a_failing_tool_becomes_a_shaped_error_not_a_traceback(wired):
    """The MCP surface composes every tool inside ``_failure_shield``.

    A CLI command awaits the undecorated function, so without this a repo with
    a ``.repowise/`` directory but no built database prints a sqlalchemy
    traceback with an **empty stdout** — the state a caller cannot tell from a
    crash, which is what every early return here exists to avoid.
    """
    engine, store, _published, repo = wired

    async def _tool():
        raise RuntimeError("boom")

    result = tool_bridge.call_tool(repo, _tool, "get_symbol")
    assert "get_symbol" in result["error"] and "boom" in result["error"]
    assert engine.disposed and store.closed


def test_an_unindexed_repo_gets_the_shields_run_init_advice(wired):
    """``_get_repo`` raises this exact LookupError; the shield shapes it."""
    _engine, _store, _published, repo = wired

    async def _tool():
        raise LookupError("No repositories found in the database")

    result = tool_bridge.call_tool(repo, _tool, "get_context")
    assert "no repowise index yet" in result["error"].lower()
    assert "repowise init" in result["remedy"]


def test_a_failed_repowise_import_points_at_the_install_not_a_server(wired):
    """The CLI imports fresh each run, so no older server is holding stale code."""
    _engine, _store, _published, repo = wired

    async def _tool():
        from repowise.core.ingestion.type_names import name_missing_from_install  # noqa: F401

    result = tool_bridge.call_tool(repo, _tool, "get_change_risk")
    assert "install is incomplete" in result["error"]
    assert "Reinstall repowise" in result["remedy"]
    assert "MCP server" not in repr(result)


def test_a_store_that_fails_to_open_still_disposes_the_engine(wired, monkeypatch):
    """Building the embedder happens before the tool call and can raise."""
    engine, _store, _published, repo = wired

    async def _explode(repo_path):
        raise RuntimeError("no embedder")

    monkeypatch.setattr(tool_bridge, "_open_vector_store", _explode)

    async def _tool():
        return {}

    result = tool_bridge.call_tool(repo, _tool, "get_why")
    assert "no embedder" in result["error"]
    assert engine.disposed


def test_a_store_that_fails_to_close_does_not_lose_the_answer(wired, monkeypatch):
    """Teardown of a store the tool already answered from is not the answer."""
    engine, store, _published, repo = wired

    async def _explode():
        raise OSError("lancedb handle already gone")

    monkeypatch.setattr(store, "close", _explode)

    async def _tool():
        return {"ok": True}

    assert _answer_only(tool_bridge.call_tool(repo, _tool, "get_symbol")) == {"ok": True}
    assert engine.disposed


def test_the_factory_runs_inside_the_loop_after_the_state_is_published(wired):
    """A coroutine built before the wiring would see the previous repo's state."""
    _engine, _store, published, repo = wired
    seen: list = []

    async def _tool():
        seen.append(dict(published))
        return {}

    tool_bridge.call_tool(repo, _tool, "get_symbol")
    assert seen and seen[0]["repo_path"] == str(repo)


@pytest.fixture
def restore_embedder_status(monkeypatch):
    """``_open_vector_store`` writes a module global that outlives the test.

    ``_state._embedder_status`` feeds ``build_meta``, so leaking a "mock"
    status here would make any later test that builds a ``_meta`` envelope see
    a degraded embedder that no test set.
    """
    from repowise.server.mcp_server import _state

    monkeypatch.setattr(_state, "_embedder_status", None, raising=False)
    return _state


def test_it_falls_back_to_an_in_memory_store_when_the_repo_has_no_lancedb(
    monkeypatch, tmp_path, restore_embedder_status
):
    from repowise.core.persistence.vector_store import InMemoryVectorStore

    monkeypatch.setattr(
        "repowise.cli.providers.embedders.resolve_embedder_for_repo", lambda p: "mock"
    )
    monkeypatch.setattr("repowise.cli.providers.embedders.build_embedder", lambda name, _p=None: object())

    import asyncio

    store = asyncio.run(tool_bridge._open_vector_store(tmp_path))
    assert isinstance(store, InMemoryVectorStore)


def test_a_repo_whose_embedder_key_went_away_is_recorded_as_degraded(
    monkeypatch, tmp_path, restore_embedder_status
):
    """``build_meta`` reads this global to set ``embedder_degraded``.

    Only the MCP server's own ``_resolve_embedder`` ever wrote it, so a CLI
    answer produced with no semantic retrieval looked identical to a healthy
    one — the exact condition the field was added for.
    """
    import asyncio

    from repowise.core.providers.embedding import KeylessEmbedder

    monkeypatch.setattr(
        "repowise.cli.providers.embedders.resolve_embedder_for_repo", lambda p: "openai"
    )
    monkeypatch.setattr(
        "repowise.cli.providers.embedders.build_embedder", lambda name, _p=None: KeylessEmbedder()
    )
    asyncio.run(tool_bridge._open_vector_store(tmp_path))

    status = restore_embedder_status._embedder_status
    assert status["degraded"] is True
    assert status["requested"] == "openai" and status["active"] == "mock"
    assert "openai" in status["reason"]


def test_a_store_one_repowise_older_than_the_models_is_still_readable(monkeypatch, tmp_path):
    """A real store missing a column the models gained must not hard-fail.

    Not a fake engine: this builds an actual SQLite store, drops a column from
    it, and drives the whole bridge. Every tool's first query is
    ``select(Repository)``, so before ``init_db`` was paired with
    ``create_engine`` here the entire CLI tool surface — ask, context, symbol,
    why, search, risk — came back as a raw ``no such column`` OperationalError
    whose shaped guidance told the caller to stop using the tool.
    """
    import asyncio
    import sqlite3

    from sqlalchemy import select

    from repowise.core.persistence import create_engine, init_db
    from repowise.core.persistence.models import Repository
    from repowise.server.mcp_server import _state

    db_file = tmp_path / "wiki.db"
    url = f"sqlite+aiosqlite:///{db_file}"

    async def _build():
        engine = create_engine(url)
        await init_db(engine)
        await engine.dispose()

    asyncio.run(_build())

    # Roll the store back one schema version, the way a store built by an
    # older repowise is. 17 columns is current, 16 is legacy.
    conn = sqlite3.connect(db_file)
    conn.execute("ALTER TABLE repositories DROP COLUMN churn_anchor_sha")
    conn.commit()
    stale_cols = {r[1] for r in conn.execute("pragma table_info(repositories)")}
    conn.close()
    assert "churn_anchor_sha" not in stale_cols

    monkeypatch.setattr("repowise.cli.helpers.get_db_url_for_repo", lambda p: url)

    async def _fake_open(repo_path):
        return None

    monkeypatch.setattr(tool_bridge, "_open_vector_store", _fake_open)

    async def _tool():
        async with _state._session_factory() as session:
            await session.execute(select(Repository).limit(1))
        return {"ok": True}

    assert _answer_only(tool_bridge.call_tool(tmp_path, _tool, "get_answer")) == {"ok": True}

    conn = sqlite3.connect(db_file)
    healed = {r[1] for r in conn.execute("pragma table_info(repositories)")}
    conn.close()
    assert "churn_anchor_sha" in healed


def test_a_store_that_cannot_be_repaired_still_serves_the_read(monkeypatch, tmp_path):
    """Reconciling is opportunistic; a failed repair must not fail the call.

    Measured while reviewing the fix: a read-only store, and a store whose
    write lock is held by a concurrent ``repowise update``, both make the
    ``ALTER TABLE`` raise. Aborting there would be a regression — a store
    already on the current schema needs no DDL and reads fine either way, so
    the repair failing says nothing about whether the read can succeed.
    """
    import asyncio

    from repowise.cli import helpers

    db = tmp_path / "wiki.db"
    db.write_text("not a database", encoding="utf-8")

    async def _explode(_engine):
        raise RuntimeError("attempt to write a readonly database")

    monkeypatch.setattr("repowise.core.persistence.init_db", _explode, raising=False)
    # Must not raise.
    asyncio.run(helpers.reconcile_schema_best_effort(f"sqlite+aiosqlite:///{db}"))


def test_the_repair_never_creates_a_store_that_did_not_exist(tmp_path):
    """A read command in an un-indexed repo must not leave a database behind.

    ``init_db`` on a fresh path materialises the full 42-table schema, so
    without this guard ``repowise ask`` in a repo that was never indexed writes
    a ~512 KB ``wiki.db`` where the user expects "not indexed yet".
    """
    import asyncio

    from repowise.cli import helpers

    db = tmp_path / "wiki.db"
    asyncio.run(helpers.reconcile_schema_best_effort(f"sqlite+aiosqlite:///{db}"))
    assert not db.exists(), "the repair created a store where none existed"


def test_the_repair_is_bounded_when_the_store_is_locked(tmp_path):
    """It must fail fast, not inherit the 30s bulk-write window.

    ``status`` opens four engines and ``doctor`` five. At the engine default a
    store locked by a concurrent ``repowise update`` stalled ``status`` for
    ~133s in silence — swallowing the exception does not swallow the wait.
    """
    import asyncio
    import sqlite3
    import time

    from repowise.cli import helpers
    from repowise.core.persistence import create_engine, init_db

    db = tmp_path / "wiki.db"
    url = f"sqlite+aiosqlite:///{db}"

    async def _build():
        engine = create_engine(url)
        await init_db(engine)
        await engine.dispose()

    asyncio.run(_build())
    # Real drift, so the repair has DDL to issue and must take the write lock.
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE repositories DROP COLUMN churn_anchor_sha")
    conn.commit()
    conn.close()

    holder = sqlite3.connect(db, isolation_level=None, timeout=1)
    holder.execute("BEGIN EXCLUSIVE")
    holder.execute("UPDATE repositories SET name = name")
    try:
        t0 = time.perf_counter()
        asyncio.run(helpers.reconcile_schema_best_effort(url))
        elapsed = time.perf_counter() - t0
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    assert elapsed < 10, (
        f"the contended repair took {elapsed:.1f}s; the engine default is 30s and "
        "status calls this four times"
    )


def test_a_repo_that_asked_for_keyless_is_not_reported_as_degraded(
    monkeypatch, tmp_path, restore_embedder_status
):
    """Resolving to the keyless embedder is a configuration, not a failure."""
    import asyncio

    from repowise.core.providers.embedding import KeylessEmbedder

    monkeypatch.setattr(
        "repowise.cli.providers.embedders.resolve_embedder_for_repo", lambda p: "mock"
    )
    monkeypatch.setattr(
        "repowise.cli.providers.embedders.build_embedder", lambda name, _p=None: KeylessEmbedder()
    )
    asyncio.run(tool_bridge._open_vector_store(tmp_path))

    assert restore_embedder_status._embedder_status["degraded"] is False


def test_a_bridged_call_is_bounded_like_an_mcp_one(wired):
    """The CLI awaits tool functions directly, so no middleware runs.

    Budgeting has to happen here or ``repowise search --format json``, which
    agents read, returns whatever the tool built.
    """
    _engine, _store, _published, repo = wired

    async def _tool():
        return {
            "results": [{"path": f"src/f{i}.py", "excerpt": "x" * 900} for i in range(80)],
            "_meta": {},
        }

    result = tool_bridge.call_tool(repo, _tool, "search_codebase")

    budget = result["_meta"]["response_budget"]
    assert budget["serialized_chars"] <= budget["limit_chars"]
    assert result["truncated"] is True
    assert len(result["results"]) < 80
    assert result["_meta"]["omitted"]["refs"]


def test_publishing_tool_state_does_not_load_the_http_stack():
    """A CLI tool call wires state per process; FastAPI costs about half a second."""
    import subprocess
    import sys

    code = (
        "import sys; from repowise.server.mcp_server._state import init_tool_state; "
        "init_tool_state(None, None, None); "
        "assert 'fastapi' not in sys.modules, 'fastapi imported'"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


class _FakeRegistry:
    def __init__(self, default_ctx, error: Exception | None = None) -> None:
        self.default_ctx = default_ctx
        self.error = error
        self.closed = False

    async def get_default(self):
        if self.error is not None:
            raise self.error
        return self.default_ctx

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def workspace(wired, monkeypatch):
    """A workspace whose member ``code`` is the wired repo, and a fake registry."""
    from types import SimpleNamespace

    from repowise.server.mcp_server import _state

    _engine, _store, published, repo = wired
    ws_root = repo / "ws"
    (repo / "code" / ".repowise").mkdir(parents=True)
    ws_root.mkdir()
    (ws_root / ".repowise-workspace.yaml").write_text(
        "version: 1\ndefault_repo: other\nrepos:\n"
        "- path: ../other\n  alias: other\n- path: ../code\n  alias: code\n"
    )
    ctx = SimpleNamespace(
        session_factory="sf", fts="fts", vector_store="vs", decision_store="vs",
        vector_store_ready=None,
    )
    registry = _FakeRegistry(ctx)
    built: list = []

    def _fake_registry(root, config, alias, db_url):
        built.append(alias)
        return registry

    monkeypatch.setattr(
        "repowise.server.mcp_server._server.workspace_registry", _fake_registry
    )
    monkeypatch.setattr(_state, "_registry", None)
    monkeypatch.setattr(_state, "_workspace_root", None)
    monkeypatch.setattr(_state, "_repo_path", None)
    return repo / "code", ws_root, registry, built, published


def test_a_workspace_call_is_served_like_the_mcp_server_then_torn_down(workspace):
    """``get_answer`` reads the other members through ``_state._registry``."""
    from repowise.server.mcp_server import _state

    code, ws_root, registry, built, published = workspace
    seen: dict = {}

    async def _tool():
        seen.update(registry=_state._registry, sf=_state._session_factory,
                    root=_state._workspace_root, repo_path=_state._repo_path)
        return {"ok": True}

    result = tool_bridge.call_tool(code, _tool, "get_answer", workspace_root=ws_root)
    assert _answer_only(result) == {"ok": True}
    # Defaults to the repo the command resolved, not the workspace's default.
    assert built == ["code"]
    assert seen == {"registry": registry, "sf": "sf", "root": str(ws_root),
                    "repo_path": str(code)}
    assert not published, "the single-repo wiring ran as well"
    assert registry.closed and _state._registry is None and _state._workspace_root is None


def test_a_repo_outside_the_workspaces_members_is_served_alone(workspace):
    from repowise.server.mcp_server import _state

    _code, ws_root, _registry, built, published = workspace
    stray = ws_root.parent / "stray"
    (stray / ".repowise").mkdir(parents=True)

    async def _tool():
        return {"registry": _state._registry}

    assert tool_bridge.call_tool(stray, _tool, "get_answer", workspace_root=ws_root)[
        "registry"
    ] is None
    assert built == [] and published["repo_path"] == str(stray)


def test_a_workspace_store_that_cannot_open_falls_back_to_the_repo_alone(workspace):
    from repowise.server.mcp_server import _state

    code, ws_root, registry, _built, published = workspace
    registry.error = LookupError("no row for this checkout")

    async def _tool():
        return {"registry": _state._registry}

    assert tool_bridge.call_tool(code, _tool, "get_answer", workspace_root=ws_root)[
        "registry"
    ] is None
    assert registry.closed and published["repo_path"] == str(code)


def test_live_reads_follow_the_served_checkout_of_a_moved_index(monkeypatch, tmp_path):
    """An index copied to another path reads its files where it is served.

    The stored ``local_path`` still names where it was built, so reading lines
    or references through it found another tree, or none. The MCP server serves
    the checkout's own row from its own path; a CLI call must do the same.
    """
    import asyncio

    from repowise.core.persistence import create_engine, init_db
    from repowise.core.persistence.database import resolve_db_url
    from repowise.core.persistence.models import Repository
    from repowise.server.mcp_server import _state
    from repowise.server.mcp_server._meta import served_root

    built_at = tmp_path / "built"
    served = tmp_path / "copy"
    (served / ".repowise").mkdir(parents=True)
    (served / ".git").mkdir()
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    monkeypatch.setattr(_state, "_repo_path", _state._repo_path)
    monkeypatch.setattr(_state, "_served_repo_id", _state._served_repo_id)

    async def _build():
        engine = create_engine(resolve_db_url(served))
        await init_db(engine)
        async with engine.begin() as conn:
            await conn.execute(
                Repository.__table__.insert().values(id="r1", name="r", local_path=str(built_at))
            )
        await engine.dispose()

    asyncio.run(_build())

    async def _fake_open(repo_path):
        return None

    monkeypatch.setattr(tool_bridge, "_open_vector_store", _fake_open)

    async def _tool():
        async with _state._session_factory() as session:
            return {"root": served_root(await session.get(Repository, "r1"))}

    assert tool_bridge.call_tool(served, _tool, "get_context")["root"] == str(served)
