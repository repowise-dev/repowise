"""No recoverable state may reach the agent as a protocol-level isError.

An early isError teaches an agent to abandon the MCP server for the whole
session, so the failure shield must convert every exception that escapes a
tool handler — including the un-init'd-repo LookupError that previously fired
on EVERY tool's first call — into a success-shaped dict with a remedy.
"""

from __future__ import annotations

import inspect

import pytest

from repowise.server.mcp_server._failure_shield import shield


@pytest.fixture
async def empty_mcp(factory, fts, vector_store):
    """MCP globals wired to an EMPTY database — the un-init'd repo state."""
    import repowise.server.mcp_server as mcp_mod

    mcp_mod._session_factory = factory
    mcp_mod._fts = fts
    mcp_mod._vector_store = vector_store
    mcp_mod._repo_path = "/tmp/test-repo"
    yield
    mcp_mod._session_factory = None
    mcp_mod._fts = None
    mcp_mod._vector_store = None
    mcp_mod._repo_path = None


@pytest.mark.asyncio
async def test_uninitialized_repo_is_success_shaped(empty_mcp):
    """The 'No repositories found' LookupError becomes guidance, not isError."""
    from repowise.server.mcp_server import get_symbol

    result = await shield(get_symbol)("pkg/mod.py::alpha")

    assert isinstance(result, dict)  # no exception escaped
    assert "no repowise index" in result["error"].lower()
    assert "repowise init" in result["remedy"]
    # The user decides whether to index; the agent must not run init itself.
    assert "user" in result["remedy"]
    # Session-scoped guidance: use built-in tools instead of retrying forever.
    assert "Read/Grep/Glob" in result["guidance"]


@pytest.mark.asyncio
async def test_unknown_repo_alias_is_success_shaped(setup_mcp):
    from repowise.server.mcp_server import get_symbol

    result = await shield(get_symbol)("pkg/mod.py::alpha", repo="no-such-repo")

    assert "no-such-repo" in result["error"]
    assert "list_repos" in result["remedy"]


@pytest.mark.asyncio
async def test_workspace_unknown_repo_valueerror_is_success_shaped():
    """Workspace mode raises ValueError (not LookupError) for a bad alias —
    caught live on a real workspace server before this branch existed."""

    async def workspace_tool(repo: str) -> dict:
        raise ValueError(f"Unknown repo {repo!r}. Available: ['a', 'b']")

    result = await shield(workspace_tool)("no-such-repo")

    assert "no-such-repo" in result["error"]
    assert "list_repos" in result["remedy"]
    assert "Retry" not in result.get("guidance", "")


@pytest.mark.asyncio
async def test_a_store_older_than_the_models_says_run_update_not_give_up():
    """Drift ``init_db`` cannot repair must not read as an internal crash.

    Every read path now reconciles *additive* drift, so a ``no such column``
    that still reaches here is the kind ``_reconcile_schema`` deliberately
    skips — a removed or renamed column, a changed type. The generic shape
    tells the caller to stop using the tool for the rest of the session, which
    is the worst possible advice when one ``repowise update`` fixes it.
    """
    from sqlalchemy.exc import OperationalError

    async def stale_store_tool() -> dict:
        raise OperationalError(
            "SELECT repositories.churn_anchor_sha FROM repositories",
            {},
            Exception("no such column: repositories.churn_anchor_sha"),
        )

    result = await shield(stale_store_tool)()

    assert "predates the installed repowise" in result["error"]
    # First line only — SQLAlchemy appends the whole compiled statement.
    assert "\n" not in result["error"]
    assert "repowise update" in result["remedy"]
    assert "user" in result["remedy"]
    assert "Retry this call once" not in result.get("guidance", "")


@pytest.mark.asyncio
async def test_an_unrelated_operationalerror_keeps_the_internal_error_shape():
    """The stale-index branch is keyed on the message, so guard the negative."""
    from sqlalchemy.exc import OperationalError

    async def locked_db_tool() -> dict:
        raise OperationalError("SELECT 1", {}, Exception("database is locked"))

    result = await shield(locked_db_tool)()

    assert "predates" not in result["error"]
    assert "Retry this call once" in result["guidance"]


@pytest.mark.asyncio
async def test_the_users_own_question_cannot_fake_a_stale_index():
    """`str()` on a SQLAlchemy error appends the statement AND its parameters.

    `get_answer` binds the caller's question as a parameter, so matching the
    full string let someone who merely ASKS about "no such column" be told
    their index is stale and to re-index — over what is really a transient
    lock. Match the driver's own message instead.
    """
    from sqlalchemy.exc import OperationalError

    async def locked_while_asking_about_columns() -> dict:
        raise OperationalError(
            "INSERT INTO answer_cache (question, payload) VALUES (?, ?)",
            ("why do I get no such column errors when I upgrade?", "{}"),
            Exception("database is locked"),
        )

    result = await shield(locked_while_asking_about_columns)()

    assert "predates" not in result["error"], result["error"]
    assert "Retry this call once" in result["guidance"]


@pytest.mark.asyncio
async def test_unexpected_exception_is_success_shaped():
    async def exploding_tool(x: int) -> dict:
        raise RuntimeError("boom")

    result = await shield(exploding_tool)(1)

    assert "RuntimeError" in result["error"]
    assert "exploding_tool" in result["error"]
    assert "Retry this call once" in result["guidance"]


@pytest.mark.asyncio
async def test_successful_result_passes_through_untouched():
    async def fine_tool() -> dict:
        return {"answer": 42}

    assert await shield(fine_tool)() == {"answer": 42}


def test_shield_preserves_signature_for_fastmcp_schema():
    async def tool_fn(symbol_id: str, context_lines: int = 0) -> dict:
        return {}

    wrapped = shield(tool_fn)
    assert wrapped.__name__ == "tool_fn"
    assert str(inspect.signature(wrapped)) == str(inspect.signature(tool_fn))


def test_sync_callables_pass_through():
    def sync_fn() -> dict:
        return {}

    assert shield(sync_fn) is sync_fn


def test_server_composes_shield_into_middleware():
    """Pin the __init__.py wiring: every registered tool goes through the
    shield. Without this, a refactor could silently drop the composition and
    reopen the isError-on-every-tool hole."""
    import inspect as _inspect

    import repowise.server.mcp_server as mcp_mod

    source = _inspect.getsource(mcp_mod)
    assert "_failure_shield" in source
    assert "_savings_instrument(_failure_shield(fn))" in source


# ---------------------------------------------------------------------------
# Import warm-up gate
#
# The deferred `import lancedb` runs on a worker thread and holds Python's
# import locks. A tool body that lazily imports while it is in flight blocks
# against it, and the event loop stops making progress — which also stops the
# asyncio timeouts that would otherwise cap the call from ever firing. The
# observed symptom was the first tool call of a session never returning. The
# shield holds calls until the import is done so the two never overlap.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_waits_for_the_lancedb_import(monkeypatch):
    """A call that arrives mid-import runs only after the import signals done."""
    import asyncio

    from repowise.server.mcp_server import _state

    ready = asyncio.Event()
    monkeypatch.setattr(_state, "_lancedb_ready", ready, raising=False)

    started = asyncio.Event()

    async def tool() -> dict:
        started.set()
        return {"ok": True}

    task = asyncio.create_task(shield(tool)())
    await asyncio.sleep(0.05)
    assert not started.is_set(), "handler body ran while the import was in flight"

    ready.set()
    assert await task == {"ok": True}
    assert started.is_set()


@pytest.mark.asyncio
async def test_no_wait_once_the_import_is_done(monkeypatch):
    """A warmed server pays nothing: the gate is a set-Event check."""
    import asyncio

    from repowise.server.mcp_server import _state

    ready = asyncio.Event()
    ready.set()
    monkeypatch.setattr(_state, "_lancedb_ready", ready, raising=False)

    async def tool() -> dict:
        return {"ok": True}

    assert await shield(tool)() == {"ok": True}


@pytest.mark.asyncio
async def test_no_gate_when_no_import_was_started(monkeypatch):
    """No background import (no event) means no wait at all."""
    from repowise.server.mcp_server import _state

    monkeypatch.setattr(_state, "_lancedb_ready", None, raising=False)

    async def tool() -> dict:
        return {"ok": True}

    assert await shield(tool)() == {"ok": True}


@pytest.mark.asyncio
async def test_gate_is_bounded(monkeypatch):
    """A pathological import degrades to a slow answer, never a stuck client."""
    import asyncio

    # Reach the module through importlib: the package re-exports `shield`
    # under the name `_failure_shield`, so attribute access finds the function
    # rather than the submodule.
    import importlib

    from repowise.server.mcp_server import _state

    shield_mod = importlib.import_module("repowise.server.mcp_server._failure_shield")

    monkeypatch.setattr(_state, "_lancedb_ready", asyncio.Event(), raising=False)
    monkeypatch.setattr(shield_mod, "_WARMUP_TIMEOUT_S", 0.05)

    async def tool() -> dict:
        return {"ok": True}

    assert await shield(tool)() == {"ok": True}
