"""Tests for API key authentication."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_no_auth_configured_allows_access(client: AsyncClient) -> None:
    """When REPOWISE_API_KEY is not set, all endpoints are open."""
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
