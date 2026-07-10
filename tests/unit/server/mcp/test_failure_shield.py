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
