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
from starlette.routing import Route

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
    gate_if_configured,
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
    return Starlette(routes=[Route("/mcp", lambda r: PlainTextResponse("served"), methods=["GET", "POST"])])


async def _get(app, path: str, headers: dict | None = None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:7338") as c:
        return await c.get(path, headers=headers or {})


async def test_mcp_no_key_is_unwrapped_and_open() -> None:
    inner = _inner_app()
    assert gate_if_configured(inner, "/mcp") is inner
    from repowise.server.mcp_server._server import mcp

    sse = mcp.sse_app()
    assert isinstance(sse, Starlette)
    async with AsyncClient(transport=ASGITransport(app=sse), base_url="http://127.0.0.1:7338") as c:
        # The SDK's own answer (no session id), not an auth refusal.
        resp = await c.post("/messages/", content=b"{}")
        assert resp.status_code == 400
        assert (await c.get("/.well-known/oauth-protected-resource")).status_code == 404


async def test_mcp_key_requires_bearer_with_rfc9728_challenge(shared_key) -> None:
    gated = gate_if_configured(_inner_app(), "/mcp")
    resp = await _get(gated, "/mcp")
    assert resp.status_code == 401
    challenge = resp.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert 'resource_metadata="http://127.0.0.1:7338/.well-known/oauth-protected-resource/mcp"' in challenge
    assert (await _get(gated, "/mcp", {"Authorization": "Bearer nope"})).status_code == 401
    ok = await _get(gated, "/mcp", {"Authorization": f"Bearer {KEY}"})
    assert ok.status_code == 200 and ok.text == "served"


async def test_mcp_metadata_document(shared_key) -> None:
    gated = gate_if_configured(_inner_app(), "/mcp")
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
        doc = (await _get(gated, path)).json()
        assert doc["resource"] == "http://127.0.0.1:7338/mcp"
        assert doc["bearer_methods_supported"] == ["header"]
        assert "authorization_servers" not in doc


async def test_mcp_metadata_lists_plugged_in_authorization_server(shared_key) -> None:
    class OAuthBacked(auth.LocalAuthenticator):
        authorization_servers = ("https://idp.example.com",)

    auth.set_authenticator(OAuthBacked())
    try:
        gated = gate_if_configured(_inner_app(), "/mcp")
        doc = (await _get(gated, "/.well-known/oauth-protected-resource")).json()
    finally:
        auth.set_authenticator(None)
    assert doc["authorization_servers"] == ["https://idp.example.com"]


async def test_mcp_real_transports_are_gated(shared_key) -> None:
    from repowise.server.mcp_server._server import mcp

    sse = mcp.sse_app()
    async with AsyncClient(transport=ASGITransport(app=sse), base_url="http://127.0.0.1:7338") as c:
        assert (await c.post("/messages/", content=b"{}")).status_code == 401
        authed = await c.post("/messages/", content=b"{}", headers={"Authorization": f"Bearer {KEY}"})
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
    assert principal_from_access_token(access) == await auth.get_authenticator().verify_token(SCOPED)
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
    assert call_denial(SHARED_KEY_PRINCIPAL, "get_context", {"repo": "beta"}) is None
