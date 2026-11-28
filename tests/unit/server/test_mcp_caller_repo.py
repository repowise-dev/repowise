"""#6468: a workspace call without ``repo`` answers about the caller's repo.

The calling session's MCP roots name the member; the configured default stays
the fallback when the client has no roots, or none inside the workspace.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from mcp.types import ListRootsResult, Root

from repowise.core.workspace.registry import RepoRegistry, caller_alias
from repowise.server.mcp_server import _caller_repo, _state
from tests.unit.workspace.test_registry import _make_workspace


@pytest.fixture
def registry(tmp_path):
    (tmp_path / "frontend" / "src").mkdir(parents=True)
    return RepoRegistry(tmp_path, _make_workspace(tmp_path, ["backend", "frontend"], "backend"))


class _Session:
    def __init__(self, roots=(), *, capable=True, fail=None):
        self.capable, self.fail, self.calls = capable, fail, 0
        self.roots = [Root(uri=r.as_uri() if hasattr(r, "as_uri") else r) for r in roots]

    def check_client_capability(self, capability):
        return self.capable

    async def list_roots(self):
        self.calls += 1
        if self.fail == "hang":
            await asyncio.sleep(10)
        if self.fail:
            raise RuntimeError("client refused")
        return ListRootsResult(roots=self.roots)


def test_caller_alias_overrides_the_configured_default(registry):
    assert registry.get_default_alias() == "backend"
    token = caller_alias.set("frontend")
    try:
        assert registry.get_default_alias() == "frontend"
        assert registry.resolve_repo_param(None) == "frontend"
    finally:
        caller_alias.reset(token)
    token = caller_alias.set("gone")  # not a member: the configured default
    try:
        assert registry.get_default_alias() == "backend"
    finally:
        caller_alias.reset(token)


@pytest.mark.parametrize("sub", ["", "src"])
async def test_a_root_in_a_member_names_it_once_per_session(registry, tmp_path, sub):
    session = _Session([tmp_path / "elsewhere", tmp_path / "frontend" / sub])
    assert await _caller_repo.session_alias(session, registry) == "frontend"
    assert await _caller_repo.session_alias(session, registry) == "frontend"
    assert session.calls == 1


@pytest.mark.parametrize(
    "session",
    [
        _Session(capable=False),
        _Session(["file:///nowhere/at/all"]),
        _Session(fail="error"),
        _Session(fail="hang"),
    ],
    ids=["no-roots-capability", "outside", "error", "timeout"],
)
async def test_no_usable_root_keeps_the_default_and_is_cached(registry, session, monkeypatch):
    monkeypatch.setattr(_caller_repo, "_ROOTS_TIMEOUT_S", 0.05)
    assert await _caller_repo.session_alias(session, registry) is None
    assert await _caller_repo.session_alias(session, registry) is None
    assert session.calls == (1 if session.capable else 0)


async def test_a_tool_call_without_repo_answers_about_the_callers_root(
    registry, tmp_path, monkeypatch
):
    from mcp.shared.memory import create_connected_server_and_client_session

    from repowise.server.mcp_server import ensure_full_surface, mcp

    @asynccontextmanager
    async def no_lifespan(_server):
        yield {}

    ensure_full_surface()
    monkeypatch.setattr(mcp._mcp_server, "lifespan", no_lifespan)
    monkeypatch.setattr(_state, "_registry", registry)

    async def default_repo(roots) -> str:
        async def list_roots(_ctx):
            return ListRootsResult(roots=[Root(uri=r.as_uri()) for r in roots])

        async with create_connected_server_and_client_session(
            mcp._mcp_server, list_roots_callback=list_roots if roots else None
        ) as client:
            reply = await client.call_tool("list_repos", {})
            assert not reply.isError, reply.content
            return json.loads(reply.content[0].text)["default_repo"]

    assert await default_repo([tmp_path / "frontend" / "src"]) == "frontend"
    assert await default_repo([]) == "backend"
    assert caller_alias.get() is None
