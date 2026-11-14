"""Plugin authorizers: repo ids on workspace MCP, read/write actions, claims."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from repowise.core.workspace.config import RepoEntry, WorkspaceConfig
from repowise.core.workspace.registry import RepoRegistry
from repowise.server import auth
from repowise.server.auth import (
    ROUTE_ACTION_ATTR,
    Action,
    Principal,
    ScopeAuthorizer,
    authorize_repo,
    authorize_tool,
    read_route,
)
from repowise.server.deps import verify_api_key
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._http_auth import (
    access_token_for,
    call_denial,
    gate,
    principal_from_access_token,
    tool_action,
)

TOKEN = "plugin-token"


class RoleAuthorizer:
    """A plugin authorizer: repos by id from claims, writes only for writers."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, tuple, Action]] = []

    def can_access_repo(self, principal, identifiers, action=Action.READ):
        identifiers = tuple(identifiers)
        self.seen.append(("repo", identifiers, action))
        if action is Action.WRITE and "writer" not in principal.claims.get("roles", ()):
            return False
        repos = principal.claims.get("repos")
        if repos is None or not identifiers:
            return True
        return any(i in repos for i in identifiers if i)

    def can_use_tool(self, principal, tool, action=Action.READ):
        self.seen.append(("tool", (tool,), action))
        return action is Action.READ or "writer" in principal.claims.get("roles", ())


class LegacyAuthorizer:
    """The original two-argument contract; passing ``action`` would TypeError."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def can_access_repo(self, principal, identifiers):
        identifiers = tuple(identifiers)
        self.calls.append(identifiers)
        return ScopeAuthorizer().can_access_repo(principal, identifiers)

    def can_use_tool(self, principal, tool):
        return ScopeAuthorizer().can_use_tool(principal, tool)


class ClaimsAuthenticator:
    authorization_servers: tuple[str, ...] = ()

    def __init__(self, principal: Principal) -> None:
        self.principal = principal

    def configured(self) -> bool:
        return True

    async def verify_token(self, token: str) -> Principal | None:
        return self.principal if token == TOKEN else None

    async def authenticate(self, request) -> Principal:
        from fastapi import HTTPException

        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            raise HTTPException(status_code=401, detail="Missing API key")
        return self.principal


@pytest.fixture
def plugin_seams():
    yield
    auth.set_authorizer(None)
    auth.set_authenticator(None)


def _reader(**claims) -> Principal:
    return Principal(id="sso:ana", kind="sso", claims={"roles": ["reader"], **claims})


def _indexed_workspace(tmp_path: Path) -> RepoRegistry:
    for alias in ("alpha", "beta"):
        db = tmp_path / alias / ".repowise" / "wiki.db"
        db.parent.mkdir(parents=True)
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE repositories (id TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO repositories VALUES (?)", (f"id-{alias}",))
        conn.close()
    config = WorkspaceConfig(
        repos=[RepoEntry(path="alpha", alias="alpha"), RepoEntry(path="beta", alias="beta")],
        default_repo="alpha",
    )
    return RepoRegistry(tmp_path, config)


# ---------------------------------------------------------------------------
# Workspace MCP: repo ids
# ---------------------------------------------------------------------------


def test_registry_resolves_any_identity_to_the_repo_id(tmp_path) -> None:
    registry = _indexed_workspace(tmp_path)
    assert registry.repo_id("alpha") == "id-alpha"
    assert registry.repo_id("BETA") == "id-beta"
    assert registry.repo_id("beta/") == "id-beta"
    assert registry.repo_id("all") is None
    assert registry.repo_id("gamma") is None


def test_plugin_scoped_by_repo_id_works_on_workspace_mcp(
    tmp_path, monkeypatch, plugin_seams
) -> None:
    monkeypatch.setattr(_state, "_registry", _indexed_workspace(tmp_path))
    authorizer = RoleAuthorizer()
    auth.set_authorizer(authorizer)
    reader = _reader(repos=["id-alpha"])

    assert call_denial(reader, "get_answer", {"repo": "alpha"}) is None
    assert call_denial(reader, "get_answer", {}) is None  # the default repo, alpha
    assert "Repository" in call_denial(reader, "get_answer", {"repo": "beta"})
    assert ("repo", ("id-alpha", "alpha"), Action.READ) in authorizer.seen


def test_builtin_scope_accepts_alias_or_id_on_workspace_mcp(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_state, "_registry", _indexed_workspace(tmp_path))
    by_alias = Principal("token:a", "token", frozenset({"alpha"}), None)
    by_id = Principal("token:i", "token", frozenset({"id-alpha"}), None)
    for principal in (by_alias, by_id):
        assert call_denial(principal, "get_answer", {"repo": "alpha"}) is None
        assert call_denial(principal, "get_answer", {"repo": "beta"}) is not None


def test_repo_all_needs_every_repo(tmp_path, monkeypatch, plugin_seams) -> None:
    monkeypatch.setattr(_state, "_registry", _indexed_workspace(tmp_path))
    auth.set_authorizer(RoleAuthorizer())
    assert call_denial(_reader(), "get_answer", {"repo": "all"}) is None
    both = _reader(repos=["id-alpha", "id-beta"])
    assert call_denial(both, "get_answer", {"repo": "all"}) is None
    one = _reader(repos=["id-alpha"])
    assert "all" in call_denial(one, "get_answer", {"repo": "all"})
    # A repository-scoped principal is still refused outright, as before.
    scoped = Principal("token:s", "token", frozenset({"alpha", "beta"}), None)
    assert "all" in call_denial(scoped, "get_answer", {"repo": "all"})


async def test_list_repos_filters_by_repo_id(tmp_path, monkeypatch, plugin_seams) -> None:
    from repowise.server.mcp_server import tool_repos

    monkeypatch.setattr(_state, "_registry", _indexed_workspace(tmp_path))
    auth.set_authorizer(RoleAuthorizer())
    reset = auth.current_principal.set(_reader(repos=["id-beta"]))
    try:
        listed = await tool_repos.list_repos()
    finally:
        auth.current_principal.reset(reset)
    assert [r["alias"] for r in listed["repos"]] == ["beta"]
    assert listed["default_repo"] is None


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def test_mutating_tools_are_writes(plugin_seams) -> None:
    assert tool_action("get_answer") is Action.READ
    assert tool_action("set_finding_status") is Action.WRITE
    assert tool_action("not_a_registered_tool") is Action.WRITE
    auth.set_authorizer(RoleAuthorizer())
    assert call_denial(_reader(), "get_answer", {}) is None
    assert "Tool" in call_denial(_reader(), "set_finding_status", {})


def _action_app() -> FastAPI:
    router = APIRouter(dependencies=[Depends(verify_api_key)])

    @router.post("/api/repos/{repo_id}/estimate")
    @read_route
    async def estimate(repo_id: str) -> dict:
        return {"ok": True}

    @router.post("/api/repos/{repo_id}/sync")
    async def sync(repo_id: str) -> dict:
        return {"ok": True}

    @router.get("/api/repos/{repo_id}/overview")
    async def overview(repo_id: str) -> dict:
        return {"ok": True}

    @router.post("/api/repos")
    async def register() -> dict:
        return {"ok": True}

    app = FastAPI()
    app.include_router(router)
    return app


async def test_read_post_route_allows_reader_and_write_is_denied(plugin_seams) -> None:
    auth.set_authenticator(ClaimsAuthenticator(_reader(repos=["r1"])))
    authorizer = RoleAuthorizer()
    auth.set_authorizer(authorizer)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    transport = ASGITransport(app=_action_app(), client=("203.0.113.7", 4242))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.post("/api/repos/r1/estimate", headers=headers)).status_code == 200
        assert (await c.get("/api/repos/r1/overview", headers=headers)).status_code == 200
        assert (await c.post("/api/repos/r1/sync", headers=headers)).status_code == 403
        assert (await c.post("/api/repos", headers=headers)).status_code == 403
        assert (await c.get("/api/repos/r2/overview", headers=headers)).status_code == 403
    assert ("repo", ("r1",), Action.READ) in authorizer.seen
    assert ("repo", (), Action.WRITE) in authorizer.seen


def test_post_routes_that_only_read_declare_it() -> None:
    from repowise.server.routers import blast_radius, changes, chat, repos
    from repowise.server.routers.code_health import related_work_routes

    for endpoint in (
        repos.generate_estimate,
        repos.preflight_index,
        chat.chat_messages,
        chat.open_chat_session,
        chat.send_chat_session_message,
        blast_radius.analyze_blast_radius,
        changes.rank_suspects,
        related_work_routes.get_related_work,
    ):
        assert getattr(endpoint, ROUTE_ACTION_ATTR) is Action.READ, endpoint.__name__
    assert not hasattr(repos.sync_repo, ROUTE_ACTION_ATTR)


# ---------------------------------------------------------------------------
# Old-signature authorizers
# ---------------------------------------------------------------------------


def test_old_signature_authorizer_is_called_without_action(monkeypatch, plugin_seams) -> None:
    legacy = LegacyAuthorizer()
    auth.set_authorizer(legacy)
    scoped = Principal("token:s", "token", frozenset({"alpha"}), frozenset({"get_answer"}))
    assert authorize_repo(scoped, ("alpha",), Action.WRITE)
    assert not authorize_tool(scoped, "get_context", Action.WRITE)

    monkeypatch.setattr(_state, "_registry", None)
    monkeypatch.setattr(_state, "_repo_path", "/work/alpha")
    monkeypatch.setattr(_state, "_served_repo_id", "id-alpha")
    assert call_denial(scoped, "get_answer", {}) is None
    assert call_denial(scoped, "get_answer", {"repo": "beta"}) is not None
    # As before, an old authorizer is not asked about an unscoped principal.
    legacy.calls.clear()
    assert call_denial(auth.SHARED_KEY_PRINCIPAL, "get_answer", {"repo": "beta"}) is None
    assert legacy.calls == []


async def test_old_signature_authorizer_on_rest(plugin_seams) -> None:
    legacy = LegacyAuthorizer()
    auth.set_authorizer(legacy)
    auth.set_authenticator(ClaimsAuthenticator(Principal("t", "token", frozenset({"r1"}))))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    transport = ASGITransport(app=_action_app(), client=("203.0.113.7", 4242))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.post("/api/repos/r1/sync", headers=headers)).status_code == 200
        assert (await c.get("/api/repos/r2/overview", headers=headers)).status_code == 403
    assert ("r1",) in legacy.calls


# ---------------------------------------------------------------------------
# Claims over MCP HTTP
# ---------------------------------------------------------------------------


def test_claims_survive_the_access_token() -> None:
    principal = Principal(
        "sso:ana", "sso", frozenset({"id-alpha"}), None, {"roles": ["reader"], "tenant": "t1"}
    )
    round_tripped = principal_from_access_token(access_token_for(principal, TOKEN))
    assert round_tripped == principal
    bare = principal_from_access_token(access_token_for(Principal("k", "shared-key"), TOKEN))
    assert bare.claims == {}


async def test_claims_reach_the_mcp_app_through_the_gate(plugin_seams) -> None:
    auth.set_authenticator(ClaimsAuthenticator(_reader(tenant="t1")))

    async def echo(request):
        principal = request.scope["state"]["principal"]
        return JSONResponse({"kind": principal.kind, "claims": dict(principal.claims)})

    gated = gate(Starlette(routes=[Route("/mcp", echo, methods=["POST"])]), "/mcp")
    async with AsyncClient(transport=ASGITransport(app=gated), base_url="http://test") as c:
        response = await c.post("/mcp", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.json() == {"kind": "sso", "claims": {"roles": ["reader"], "tenant": "t1"}}


def test_kwargs_and_unhashable_authorizers_get_the_action() -> None:
    seen: list = []

    class KwargsAuthorizer:
        def can_access_repo(self, principal, identifiers, **kwargs):
            seen.append(kwargs.get("action"))
            return True

        def can_use_tool(self, principal, tool, **kwargs):
            seen.append(kwargs.get("action"))
            return True

    class Unhashable:
        __hash__ = None

        def __call__(self, principal, identifiers, action=Action.READ):
            seen.append(action)
            return False

    class UnhashableAuthorizer:
        can_access_repo = Unhashable()

        def can_use_tool(self, principal, tool):
            return True

    p = Principal("k", "token")
    assert authorize_repo(p, ("r",), Action.WRITE, KwargsAuthorizer())
    assert authorize_tool(p, "t", Action.WRITE, KwargsAuthorizer())
    assert not authorize_repo(p, ("r",), Action.WRITE, UnhashableAuthorizer())
    assert seen == [Action.WRITE, Action.WRITE, Action.WRITE]
