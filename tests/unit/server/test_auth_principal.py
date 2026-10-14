"""Principal, scoped tokens, and the shared auth check on REST, MCP HTTP and webhooks."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Security
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route

import repowise.server.deps as deps
from repowise.core.persistence.models import GenerationJob, Repository
from repowise.server import auth
from repowise.server.auth import (
    LOCAL_PRINCIPAL,
    SHARED_KEY_PRINCIPAL,
    Principal,
    ScopeAuthorizer,
    hash_token,
)
from repowise.server.mcp_server import _http_auth, _state
from repowise.server.mcp_server._http_auth import (
    PrincipalTokenVerifier,
    access_token_for,
    call_denial,
    gate,
    metadata_route,
    principal_from_access_token,
)

KEY = "shared-secret"
SCOPED = "scoped-token-value"


def _write_tokens(path: Path, *entries: dict) -> None:
    import yaml

    path.write_text(yaml.safe_dump({"tokens": list(entries)}), encoding="utf-8")


@pytest.fixture
def tokens_file(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "tokens.yaml"
    monkeypatch.setenv(auth.TOKENS_FILE_ENV, str(path))
    return path


@pytest.fixture
def shared_key(monkeypatch) -> str:
    monkeypatch.setattr(deps, "_API_KEY", KEY)
    return KEY


@pytest.fixture
async def remote_client(app) -> AsyncClient:
    transport = ASGITransport(app=app, client=("203.0.113.7", 4242))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Principal and authorizer
# ---------------------------------------------------------------------------


def test_unrestricted_principal_reaches_everything() -> None:
    authz = ScopeAuthorizer()
    assert authz.can_access_repo(SHARED_KEY_PRINCIPAL, ("any",))
    assert authz.can_use_tool(LOCAL_PRINCIPAL, "get_answer")


def test_scoped_principal_allow_and_deny() -> None:
    authz = ScopeAuthorizer()
    p = Principal("token:ci", "token", frozenset({"alpha"}), frozenset({"get_answer"}))
    assert authz.can_access_repo(p, ("id-1", "alpha"))
    assert not authz.can_access_repo(p, ("id-2", "beta"))
    assert not authz.can_access_repo(p, (None,))
    assert authz.can_use_tool(p, "get_answer")
    assert not authz.can_use_tool(p, "get_context")


# ---------------------------------------------------------------------------
# Authenticator: shared key and scoped tokens
# ---------------------------------------------------------------------------


async def test_no_credentials_means_not_configured() -> None:
    authn = auth.LocalAuthenticator()
    assert not authn.configured()
    assert await authn.verify_token("anything") is None


async def test_shared_key_verifies_to_shared_principal(shared_key) -> None:
    authn = auth.LocalAuthenticator()
    assert authn.configured()
    assert await authn.verify_token(KEY) == SHARED_KEY_PRINCIPAL
    assert await authn.verify_token("wrong") is None


async def test_scoped_token_carries_its_scopes(tokens_file) -> None:
    _write_tokens(
        tokens_file,
        {"id": "ci", "sha256": hash_token(SCOPED), "repos": ["alpha"], "tools": ["get_answer"]},
        {"id": "ops", "sha256": hash_token("ops-token")},
    )
    authn = auth.LocalAuthenticator()
    assert authn.configured()
    ci = await authn.verify_token(SCOPED)
    assert ci == Principal("token:ci", "token", frozenset({"alpha"}), frozenset({"get_answer"}))
    ops = await authn.verify_token("ops-token")
    assert ops is not None and ops.repo_scope is None and ops.tool_scope is None
    assert await authn.verify_token("not-a-token") is None


async def test_malformed_tokens_file_fails_closed(tokens_file, shared_key) -> None:
    tokens_file.write_text("tokens:\n  - id: x\n    sha256: short\n", encoding="utf-8")
    authn = auth.LocalAuthenticator()
    assert authn.configured()
    assert await authn.verify_token(SCOPED) is None
    assert await authn.verify_token(KEY) == SHARED_KEY_PRINCIPAL


def test_resolver_override_and_restore() -> None:
    class Custom(auth.LocalAuthenticator):
        pass

    custom = Custom()
    auth.set_authenticator(custom)
    try:
        assert auth.get_authenticator() is custom
    finally:
        auth.set_authenticator(None)
    assert isinstance(auth.get_authenticator(), auth.LocalAuthenticator)
    assert not isinstance(auth.get_authenticator(), Custom)


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------


async def test_rest_no_key_local_is_open_and_local_principal(client) -> None:
    assert (await client.get("/api/repos")).status_code == 200
    from starlette.requests import Request

    scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "client": ("127.0.0.1", 1)}
    assert await deps.verify_api_key(deps.SecurityScopes(), Request(scope), None) == LOCAL_PRINCIPAL


async def test_rest_no_key_remote_refused_unchanged(remote_client) -> None:
    resp = await remote_client.get("/api/repos")
    assert resp.status_code == 403
    assert resp.json()["detail"] == (
        "Server is reachable from the network but REPOWISE_API_KEY "
        "is not set. Set REPOWISE_API_KEY or bind to 127.0.0.1."
    )


async def test_rest_shared_key_unchanged(client, shared_key) -> None:
    assert (await client.get("/api/repos")).json()["detail"] == "Missing API key"
    bad = await client.get("/api/repos", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401 and bad.json()["detail"] == "Invalid API key"
    ok = await client.get("/api/repos", headers={"Authorization": f"Bearer {KEY}"})
    assert ok.status_code == 200


async def test_rest_tokens_file_alone_turns_auth_on(client, tokens_file) -> None:
    _write_tokens(tokens_file, {"id": "ci", "sha256": hash_token(SCOPED)})
    resp = await client.get("/api/repos")
    assert resp.status_code == 401 and resp.json()["detail"] == "Missing API key"
    ok = await client.get("/api/repos", headers={"Authorization": f"Bearer {SCOPED}"})
    assert ok.status_code == 200


async def _add_repo(session_factory, name: str, url: str = "") -> str:
    async with session_factory() as session:
        repo = Repository(name=name, url=url, local_path=f"/tmp/{name}", default_branch="main")
        session.add(repo)
        await session.commit()
        return repo.id


async def test_rest_repo_scope(client, session_factory, tokens_file) -> None:
    alpha = await _add_repo(session_factory, "alpha")
    beta = await _add_repo(session_factory, "beta")
    _write_tokens(tokens_file, {"id": "ci", "sha256": hash_token(SCOPED), "repos": ["alpha"]})
    headers = {"Authorization": f"Bearer {SCOPED}"}

    assert (await client.get(f"/api/repos/{alpha}", headers=headers)).status_code == 200
    denied = await client.get(f"/api/repos/{beta}", headers=headers)
    assert denied.status_code == 403
    listed = await client.get("/api/repos", headers=headers)
    assert [r["name"] for r in listed.json()] == ["alpha"]
    # A route that names no repo is refused for a repo-scoped token.
    assert (await client.get("/api/meta/version", headers=headers)).status_code == 403


async def test_rest_security_scopes_check_tool_scope(tokens_file) -> None:
    _write_tokens(tokens_file, {"id": "ci", "sha256": hash_token(SCOPED), "tools": ["get_answer"]})
    app = FastAPI()

    @app.get("/answer", dependencies=[Security(deps.verify_api_key, scopes=["tool:get_answer"])])
    async def answer():
        return {}

    @app.get("/risk", dependencies=[Security(deps.verify_api_key, scopes=["tool:get_risk"])])
    async def risk():
        return {}

    headers = {"Authorization": f"Bearer {SCOPED}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/answer", headers=headers)).status_code == 200
        assert (await c.get("/risk", headers=headers)).status_code == 403


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

_PUSH = {
    "ref": "refs/heads/main",
    "repository": {"clone_url": "https://github.com/org/alpha.git"},
}
_GH_HEADERS = {"X-GitHub-Event": "push", "Content-Type": "application/json"}


async def test_webhook_no_key_local_unchanged(client, session_factory, monkeypatch) -> None:
    monkeypatch.delenv("REPOWISE_GITHUB_WEBHOOK_SECRET", raising=False)
    await _add_repo(session_factory, "alpha", "https://github.com/org/alpha")
    with patch("repowise.server.routers.webhooks._launch_webhook_job"):
        resp = await client.post("/api/webhooks/github", content=json.dumps(_PUSH), headers=_GH_HEADERS)
    assert resp.status_code == 200
    async with session_factory() as session:
        assert (await session.execute(select(GenerationJob))).scalar_one() is not None


async def test_webhook_no_secret_remote_needs_bearer(remote_client, monkeypatch, shared_key) -> None:
    monkeypatch.delenv("REPOWISE_GITHUB_WEBHOOK_SECRET", raising=False)
    body = json.dumps(_PUSH)
    refused = await remote_client.post("/api/webhooks/github", content=body, headers=_GH_HEADERS)
    assert refused.status_code == 403
    assert refused.json()["detail"].startswith("Webhook secret not configured.")
    ok = await remote_client.post(
        "/api/webhooks/github",
        content=body,
        headers={**_GH_HEADERS, "Authorization": f"Bearer {KEY}"},
    )
    assert ok.status_code == 200


async def test_webhook_scoped_token_out_of_scope(client, session_factory, tokens_file, monkeypatch) -> None:
    monkeypatch.delenv("REPOWISE_GITHUB_WEBHOOK_SECRET", raising=False)
    await _add_repo(session_factory, "alpha", "https://github.com/org/alpha")
    _write_tokens(tokens_file, {"id": "ci", "sha256": hash_token(SCOPED), "repos": ["beta"]})
    with patch("repowise.server.routers.webhooks._launch_webhook_job"):
        resp = await client.post(
            "/api/webhooks/github",
            content=json.dumps(_PUSH),
            headers={**_GH_HEADERS, "Authorization": f"Bearer {SCOPED}"},
        )
    assert resp.status_code == 403
    async with session_factory() as session:
        assert (await session.execute(select(GenerationJob))).first() is None


async def test_webhook_signed_delivery_needs_no_bearer(client, monkeypatch, shared_key) -> None:
    import hashlib
    import hmac

    monkeypatch.setenv("REPOWISE_GITHUB_WEBHOOK_SECRET", "s3cret")
    body = json.dumps(_PUSH).encode()
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    resp = await client.post(
        "/api/webhooks/github", content=body, headers={**_GH_HEADERS, "X-Hub-Signature-256": sig}
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# MCP HTTP
# ---------------------------------------------------------------------------


def _inner_app() -> Starlette:
    async def echo(request):
        principal = request.scope.get("state", {}).get("principal")
        return PlainTextResponse(principal.id if principal else "served")

    return Starlette(
        routes=[
            Route("/mcp", echo, methods=["GET", "POST"]),
            Route("/messages/", echo, methods=["POST"]),
        ]
    )


async def _get(app, path: str, headers: dict | None = None, method: str = "GET"):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:7338") as c:
        return await c.request(method, path, headers=headers or {})


async def test_mcp_no_key_passes_straight_through() -> None:
    gated = gate(_inner_app(), "/mcp")
    assert (await _get(gated, "/mcp")).text == "served"
    assert (await _get(gated, "/.well-known/oauth-protected-resource")).status_code == 404
    from repowise.server.mcp_server._server import mcp

    transport = ASGITransport(app=mcp.sse_app())
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:7338") as c:
        # The SDK's own answer (no session id), not an auth refusal.
        assert (await c.post("/messages/", content=b"{}")).status_code == 400
        assert (await c.get("/.well-known/oauth-protected-resource")).status_code == 404


async def test_mcp_key_requires_bearer_with_rfc9728_challenge(shared_key) -> None:
    gated = gate(_inner_app(), "/mcp")
    resp = await _get(gated, "/mcp")
    assert resp.status_code == 401
    challenge = resp.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    url = "http://127.0.0.1:7338/.well-known/oauth-protected-resource/mcp"
    assert f'resource_metadata="{url}"' in challenge
    assert (await _get(gated, "/mcp", {"Authorization": "Bearer nope"})).status_code == 401
    ok = await _get(gated, "/mcp", {"Authorization": f"Bearer {KEY}"})
    assert ok.status_code == 200 and ok.text == "shared-key"


async def test_mcp_gate_follows_config_per_request(tokens_file) -> None:
    gated = gate(_inner_app(), "/mcp")
    assert (await _get(gated, "/mcp")).text == "served"
    _write_tokens(tokens_file, {"id": "ci", "sha256": hash_token(SCOPED)})
    assert (await _get(gated, "/mcp")).status_code == 401


async def test_mcp_sse_messages_carry_the_principal(tokens_file) -> None:
    _write_tokens(tokens_file, {"id": "ci", "sha256": hash_token(SCOPED), "tools": ["get_answer"]})
    gated = gate(_inner_app(), "/sse")
    headers = {"Authorization": f"Bearer {SCOPED}"}
    resp = await _get(gated, "/messages/", headers, method="POST")
    assert resp.status_code == 200 and resp.text == "token:ci"
    assert (await _get(gated, "/messages/", method="POST")).status_code == 401


async def test_mcp_metadata_document(shared_key) -> None:
    gated = gate(_inner_app(), "/mcp")
    for path in (
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
    ):
        doc = (await _get(gated, path)).json()
        assert doc["resource"] == "http://127.0.0.1:7338/mcp"
        assert doc["bearer_methods_supported"] == ["header"]
        assert "authorization_servers" not in doc


async def test_mcp_metadata_lists_plugged_in_authorization_server(shared_key) -> None:
    class OAuthBacked(auth.LocalAuthenticator):
        authorization_servers = ("https://idp.example.com",)

    auth.set_authenticator(OAuthBacked())
    try:
        gated = gate(_inner_app(), "/mcp")
        doc = (await _get(gated, "/.well-known/oauth-protected-resource")).json()
    finally:
        auth.set_authenticator(None)
    assert doc["authorization_servers"] == ["https://idp.example.com"]


async def test_mcp_hostile_host_header_is_not_reflected(shared_key) -> None:
    gated = gate(_inner_app(), "/mcp")
    resp = await _get(gated, "/mcp", {"Host": 'evil"x, foo="bar'})
    challenge = resp.headers["www-authenticate"]
    assert "evil" not in challenge and challenge.count('"') == 6
    doc = (await _get(gated, "/.well-known/oauth-protected-resource", {"Host": 'a"b'})).json()
    assert '"' not in doc["resource"]


async def test_mcp_public_url_wins(shared_key, monkeypatch) -> None:
    monkeypatch.setenv(_http_auth.PUBLIC_URL_ENV, "https://code.example.com/rw/")
    gated = gate(_inner_app(), "/mcp")
    challenge = (await _get(gated, "/mcp")).headers["www-authenticate"]
    url = "https://code.example.com/.well-known/oauth-protected-resource/rw/mcp"
    assert f'resource_metadata="{url}"' in challenge
    doc = (await _get(gated, "/.well-known/oauth-protected-resource")).json()
    assert doc["resource"] == "https://code.example.com/rw/mcp"


async def test_mcp_under_a_sub_path_discovers_at_host_root(shared_key) -> None:
    host = Starlette(
        routes=[metadata_route("/sub/mcp"), Mount("/sub", app=gate(_inner_app(), "/mcp"))]
    )
    challenge = (await _get(host, "/sub/mcp")).headers["www-authenticate"]
    url = "http://127.0.0.1:7338/.well-known/oauth-protected-resource/sub/mcp"
    assert f'resource_metadata="{url}"' in challenge
    doc = (await _get(host, "/.well-known/oauth-protected-resource/sub/mcp")).json()
    assert doc["resource"] == "http://127.0.0.1:7338/sub/mcp"
    ok = await _get(host, "/sub/mcp", {"Authorization": f"Bearer {KEY}"})
    assert ok.text == "shared-key"


async def test_mcp_real_transports_are_gated(shared_key) -> None:
    from repowise.server.mcp_server._server import mcp

    sse = mcp.sse_app()
    async with AsyncClient(transport=ASGITransport(app=sse), base_url="http://127.0.0.1:7338") as c:
        assert (await c.post("/messages/", content=b"{}")).status_code == 401
        headers = {"Authorization": f"Bearer {KEY}"}
        authed = await c.post("/messages/", content=b"{}", headers=headers)
        assert authed.status_code == 400  # past the gate, into the SDK
    assert isinstance(mcp.streamable_http_app(), _http_auth.BearerGate)


async def test_token_verifier_round_trips_principal(tokens_file) -> None:
    _write_tokens(
        tokens_file,
        {"id": "ci", "sha256": hash_token(SCOPED), "repos": ["alpha"], "tools": ["get_answer"]},
    )
    access = await PrincipalTokenVerifier().verify_token(SCOPED)
    assert access is not None and access.client_id == "token:ci"
    assert access.scopes == ["tool:get_answer"]
    expected = await auth.get_authenticator().verify_token(SCOPED)
    assert principal_from_access_token(access) == expected
    assert await PrincipalTokenVerifier().verify_token("nope") is None
    unrestricted = principal_from_access_token(access_token_for(SHARED_KEY_PRINCIPAL, KEY))
    assert unrestricted.tool_scope is None and unrestricted.repo_scope is None


def test_mcp_call_denial_tool_and_repo_scope(monkeypatch) -> None:
    monkeypatch.setattr(_state, "_registry", None)
    monkeypatch.setattr(_state, "_repo_path", "/work/alpha")
    monkeypatch.setattr(_state, "_served_repo_id", "id-alpha")
    p = Principal("token:ci", "token", frozenset({"alpha"}), frozenset({"get_answer"}))
    assert call_denial(p, "get_answer", {}) is None
    assert "Tool" in call_denial(p, "get_context", {})
    assert "Repository" in call_denial(p, "get_answer", {"repo": "beta"})
    assert "all" in call_denial(p, "get_answer", {"repo": "all"})
    assert call_denial(SHARED_KEY_PRINCIPAL, "get_context", {"repo": "beta"}) is None


class _Workspace:
    workspace_root = Path(".")

    def get_all_aliases(self):
        return ["alpha", "beta"]

    def get_default_alias(self):
        return "alpha"

    def resolve_repo_param(self, repo):
        if repo is None:
            return "alpha"
        if repo == "all":
            return ["alpha", "beta"]
        if repo in ("alpha", "beta"):
            return repo
        raise ValueError(repo)


def test_mcp_workspace_aliases_and_all_refused_outside_scope(monkeypatch) -> None:
    monkeypatch.setattr(_state, "_registry", _Workspace())
    p = Principal("token:ci", "token", frozenset({"alpha"}), None)
    assert call_denial(p, "get_answer", {"repo": "alpha"}) is None
    assert call_denial(p, "get_answer", {"repo": "beta"}) is not None
    assert call_denial(p, "get_answer", {"repo": "all"}) is not None
    assert call_denial(p, "get_answer", {"repo": "unknown"}) is not None
    assert call_denial(p, "list_repos", {}) is None


async def test_mcp_list_repos_filters_to_scope(monkeypatch) -> None:
    from repowise.server.mcp_server import tool_repos

    monkeypatch.setattr(_state, "_registry", _Workspace())
    monkeypatch.setattr(
        tool_repos,
        "_workspace_repos",
        lambda _r: [
            {"alias": "alpha", "path": "alpha", "is_default": True},
            {"alias": "beta", "path": "beta", "is_default": False},
        ],
    )
    async def listed(scope):
        principal = Principal("token:ci", "token", frozenset(scope), None)
        reset = auth.current_principal.set(principal)
        try:
            return await tool_repos.list_repos()
        finally:
            auth.current_principal.reset(reset)

    beta_only = await listed({"beta"})
    assert [r["alias"] for r in beta_only["repos"]] == ["beta"]
    assert beta_only["default_repo"] is None
    alpha_only = await listed({"alpha"})
    assert alpha_only["default_repo"] == "alpha"
    nothing = await listed({"gamma"})
    assert nothing["repos"] == [] and nothing["default_repo"] is None
    unrestricted = await tool_repos.list_repos()
    assert len(unrestricted["repos"]) == 2 and unrestricted["default_repo"] == "alpha"


async def test_mcp_list_repos_single_repo_respects_scope(monkeypatch) -> None:
    from repowise.server.mcp_server import tool_repos

    monkeypatch.setattr(_state, "_registry", None)
    monkeypatch.setattr(_state, "_repo_path", "/work/alpha")
    monkeypatch.setattr(_state, "_served_repo_id", "id-alpha")

    async def listed(principal):
        reset = auth.current_principal.set(principal)
        try:
            return await tool_repos.list_repos()
        finally:
            auth.current_principal.reset(reset)

    out_of_scope = await listed(Principal("token:ci", "token", frozenset({"beta"}), None))
    assert out_of_scope["repos"] == [] and out_of_scope["default_repo"] is None
    by_name = await listed(Principal("token:ci", "token", frozenset({"alpha"}), None))
    assert len(by_name["repos"]) == 1 and by_name["default_repo"] == "default"
    by_id = await listed(Principal("token:ci", "token", frozenset({"id-alpha"}), None))
    assert len(by_id["repos"]) == 1
    unrestricted = await tool_repos.list_repos()
    assert unrestricted["default_repo"] == "default" and len(unrestricted["repos"]) == 1


# ---------------------------------------------------------------------------
# Tool-scoped tokens on REST and chat
# ---------------------------------------------------------------------------


async def test_rest_refuses_tool_scoped_token_outside_chat(
    app, client, session_factory, tokens_file
) -> None:
    from repowise.server.routers import chat as chat_router

    app.include_router(chat_router.router)
    alpha = await _add_repo(session_factory, "alpha")
    _write_tokens(tokens_file, {"id": "ci", "sha256": hash_token(SCOPED), "tools": ["get_answer"]})
    headers = {"Authorization": f"Bearer {SCOPED}"}
    assert (await client.get("/api/repos", headers=headers)).status_code == 403
    assert (await client.get(f"/api/repos/{alpha}", headers=headers)).status_code == 403
    patched = await client.patch(f"/api/repos/{alpha}", json={"name": "x"}, headers=headers)
    assert patched.status_code == 403
    chat = await client.get(f"/api/repos/{alpha}/chat/conversations", headers=headers)
    assert chat.status_code == 200


def _fake_entry(name: str, seen: dict):
    from types import SimpleNamespace

    async def fn(repo: str | None = None):
        seen["repo"] = repo
        seen["principal"] = auth.current_principal.get()
        return {"ok": True}

    return SimpleNamespace(name=name, safety="read_only", fn=fn)


async def test_chat_execution_enforces_tool_scope_and_pins_repo() -> None:
    from repowise.server.chat_tools import execute_entry

    seen: dict = {}
    tools_only = Principal("token:t", "token", None, frozenset({"get_answer"}))
    refused = await execute_entry(_fake_entry("get_context", seen), {}, principal=tools_only)
    assert refused["error_code"] == "tool_not_in_scope" and not seen

    repo_scoped = Principal("token:r", "token", frozenset({"alpha"}), None)
    entry = _fake_entry("get_answer", seen)
    await execute_entry(entry, {"repo": "all"}, repo="alpha", principal=repo_scoped)
    assert seen["repo"] == "alpha" and seen["principal"] is repo_scoped
    await execute_entry(entry, {"repo": "beta"}, repo=None, principal=repo_scoped, repo_id="id-1")
    assert seen["repo"] == "id-1"
    # Unrestricted callers keep the model's choice, as before.
    await execute_entry(entry, {"repo": "beta"}, repo=None, principal=LOCAL_PRINCIPAL)
    assert seen["repo"] == "beta"


def test_chat_catalog_follows_tool_scope() -> None:
    from repowise.server.chat_tools import get_tool_catalog, get_tool_schemas_for_llm

    full = {t.entry.name for t in get_tool_catalog(None)}
    assert "get_answer" in full and len(full) > 1
    tools_only = Principal("token:t", "token", None, frozenset({"get_answer"}))
    assert [t.entry.name for t in get_tool_catalog(None, tools_only)] == ["get_answer"]
    schemas = get_tool_schemas_for_llm(None, tools_only)
    assert [s["function"]["name"] for s in schemas] == ["get_answer"]
    assert {t.entry.name for t in get_tool_catalog(None, LOCAL_PRINCIPAL)} == full


# ---------------------------------------------------------------------------
# Tokens file edge cases, objects outside the scope, job stream
# ---------------------------------------------------------------------------


async def test_unknown_token_key_fails_closed(tokens_file, shared_key) -> None:
    _write_tokens(tokens_file, {"id": "ci", "sha256": hash_token(SCOPED), "repo": ["alpha"]})
    authn = auth.LocalAuthenticator()
    assert await authn.verify_token(SCOPED) is None
    assert await authn.verify_token(KEY) == SHARED_KEY_PRINCIPAL


async def test_token_expiry(client, tokens_file) -> None:
    _write_tokens(
        tokens_file,
        {"id": "old", "sha256": hash_token("old-token"), "expires_at": "2020-01-01T00:00:00Z"},
        {"id": "new", "sha256": hash_token(SCOPED), "expires_at": "2999-01-01T00:00:00+00:00"},
    )
    old = {"Authorization": "Bearer old-token"}
    assert (await client.get("/api/repos", headers=old)).status_code == 401
    new = {"Authorization": f"Bearer {SCOPED}"}
    assert (await client.get("/api/repos", headers=new)).status_code == 200
    assert (await _get(gate(_inner_app(), "/mcp"), "/mcp", old)).status_code == 401


async def test_unreadable_tokens_file_fails_closed(tokens_file, monkeypatch) -> None:
    real_stat = Path.stat

    def stat(self, *args, **kwargs):
        if self == tokens_file:
            raise PermissionError("denied")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    authn = auth.LocalAuthenticator()
    assert authn.configured()
    assert await authn.verify_token(SCOPED) is None


async def test_non_ascii_bearer_is_401_not_500(client, shared_key) -> None:
    headers = {"Authorization": "Bearer café".encode("latin-1")}
    resp = await client.get("/api/repos", headers=headers)
    assert resp.status_code == 401


async def test_page_lookup_outside_scope_is_404(client, session_factory, tokens_file) -> None:
    from repowise.core.persistence import crud

    alpha = await _add_repo(session_factory, "alpha")
    beta = await _add_repo(session_factory, "beta")
    async with session_factory() as session:
        await crud.upsert_page(
            session,
            page_id="file_page:b.py",
            repository_id=beta,
            page_type="file_page",
            title="b",
            content="b",
            target_path="b.py",
            source_hash="h",
            model_name="m",
            provider_name="p",
        )
        await session.commit()
    _write_tokens(
        tokens_file,
        {"id": "ci", "sha256": hash_token(SCOPED), "repos": ["alpha"]},
        {"id": "ops", "sha256": hash_token("ops-token")},
    )
    url = f"/api/pages/lookup?page_id=file_page:b.py&repo_id={alpha}"
    scoped = await client.get(url, headers={"Authorization": f"Bearer {SCOPED}"})
    assert scoped.status_code == 404
    # Unrestricted callers are unchanged.
    assert (await client.get(url, headers={"Authorization": "Bearer ops-token"})).status_code == 200


async def test_job_stream_refuses_tool_scoped_bearer(client, session_factory, tokens_file) -> None:
    from repowise.core.persistence import crud

    alpha = await _add_repo(session_factory, "alpha")
    async with session_factory() as session:
        job = await crud.upsert_generation_job(
            session, repository_id=alpha, status="completed", config={}
        )
        await session.commit()
        job_id = job.id
    _write_tokens(
        tokens_file,
        {"id": "ci", "sha256": hash_token(SCOPED), "tools": ["get_answer"]},
        {"id": "ops", "sha256": hash_token("ops-token")},
    )
    url = f"/api/jobs/{job_id}/stream"
    refused = await client.get(url, headers={"Authorization": f"Bearer {SCOPED}"})
    assert refused.status_code == 401
    assert (await client.get(url, headers={"Authorization": "Bearer ops-token"})).status_code == 200


# ---------------------------------------------------------------------------
# Seam registry
# ---------------------------------------------------------------------------


def test_default_authenticator_and_authorizer_are_local(monkeypatch) -> None:
    monkeypatch.delenv("REPOWISE_SEAM_AUTHENTICATOR", raising=False)
    monkeypatch.delenv("REPOWISE_SEAM_AUTHORIZER", raising=False)
    assert auth.authenticators.default == "local"
    assert type(auth.get_authenticator()) is auth.LocalAuthenticator
    assert type(auth.get_authorizer()) is ScopeAuthorizer


def test_plugin_authenticator_selected_by_env_var(tmp_path, monkeypatch) -> None:
    from repowise.core.registry import plugins

    (tmp_path / "acme_auth_plugin.py").write_text(
        "from repowise.server import auth\n"
        "class Acme(auth.LocalAuthenticator):\n"
        "    authorization_servers = ('https://idp.acme.test',)\n"
        "def register():\n"
        "    auth.authenticators.register('acme', Acme())\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("REPOWISE_PLUGINS", "acme_auth_plugin:register")
    plugins.reset()
    try:
        assert type(auth.get_authenticator()) is auth.LocalAuthenticator
        monkeypatch.setenv("REPOWISE_SEAM_AUTHENTICATOR", "acme")
        assert type(auth.get_authenticator()).__name__ == "Acme"
        monkeypatch.setenv("REPOWISE_SEAM_AUTHENTICATOR", "nope")
        with pytest.raises(KeyError, match="nope"):
            auth.get_authenticator()
    finally:
        monkeypatch.delenv("REPOWISE_SEAM_AUTHENTICATOR")
        plugins.reset()
