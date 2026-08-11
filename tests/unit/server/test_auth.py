"""Tests for API key authentication."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def remote_client(app) -> AsyncClient:
    """A client whose peer address is off-host, as a LAN caller would be."""
    transport = ASGITransport(app=app, client=("203.0.113.7", 4242))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_no_auth_configured_allows_local_access(client: AsyncClient) -> None:
    """When REPOWISE_API_KEY is not set, local callers are served."""
    resp = await client.get("/api/repos")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auth_rejects_missing_key(client: AsyncClient) -> None:
    """When API key is configured, requests without it are rejected."""
    import repowise.server.deps as deps_mod

    original = deps_mod._API_KEY
    deps_mod._API_KEY = "test-secret-key"
    try:
        resp = await client.get("/api/repos")
        assert resp.status_code == 401
        assert "Missing API key" in resp.json()["detail"]
    finally:
        deps_mod._API_KEY = original


@pytest.mark.asyncio
async def test_auth_rejects_wrong_key(client: AsyncClient) -> None:
    """When API key is configured, a wrong key is rejected."""
    import repowise.server.deps as deps_mod

    original = deps_mod._API_KEY
    deps_mod._API_KEY = "test-secret-key"
    try:
        resp = await client.get(
            "/api/repos",
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 401
        assert "Invalid API key" in resp.json()["detail"]
    finally:
        deps_mod._API_KEY = original


@pytest.mark.asyncio
async def test_auth_accepts_correct_key(client: AsyncClient) -> None:
    """When API key is configured, the correct key grants access."""
    import repowise.server.deps as deps_mod

    original = deps_mod._API_KEY
    deps_mod._API_KEY = "test-secret-key"
    try:
        resp = await client.get(
            "/api/repos",
            headers={"Authorization": "Bearer test-secret-key"},
        )
        assert resp.status_code == 200
    finally:
        deps_mod._API_KEY = original


@pytest.mark.asyncio
async def test_keyless_server_refuses_remote_caller(remote_client: AsyncClient) -> None:
    """Fail closed on the peer address, not on what REPOWISE_HOST claims.

    `repowise serve --host 0.0.0.0` never set REPOWISE_HOST, so the guard that
    was supposed to catch a network-exposed bind never fired and every
    endpoint was open to the LAN.
    """
    resp = await remote_client.get("/api/repos")
    assert resp.status_code == 403
    assert "REPOWISE_API_KEY" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_remote_caller_with_key_is_served(remote_client: AsyncClient) -> None:
    """The key is what makes a network-exposed server usable."""
    import repowise.server.deps as deps_mod

    original = deps_mod._API_KEY
    deps_mod._API_KEY = "test-secret-key"
    try:
        resp = await remote_client.get(
            "/api/repos",
            headers={"Authorization": "Bearer test-secret-key"},
        )
        assert resp.status_code == 200
    finally:
        deps_mod._API_KEY = original


@pytest.mark.asyncio
async def test_job_stream_is_closed_to_remote_callers(remote_client: AsyncClient) -> None:
    """The SSE route authorizes separately, so it needs the same peer test."""
    resp = await remote_client.get("/api/jobs/does-not-exist/stream")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health_bypasses_auth(client: AsyncClient) -> None:
    """The /health endpoint should always be accessible, even with auth enabled."""
    import repowise.server.deps as deps_mod

    original = deps_mod._API_KEY
    deps_mod._API_KEY = "test-secret-key"
    try:
        resp = await client.get("/health")
        assert resp.status_code == 200
    finally:
        deps_mod._API_KEY = original
