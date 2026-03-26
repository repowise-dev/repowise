"""Tests for /api/webhooks endpoints."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from tests.unit.server.conftest import create_test_repo


@pytest.mark.asyncio
async def test_github_webhook_no_secret(client: AsyncClient) -> None:
    """Without a secret configured, any payload is accepted."""
    payload = {
        "ref": "refs/heads/main",
        "repository": {"clone_url": "https://github.com/example/test-repo.git"},
    }
    resp = await client.post(
        "/api/webhooks/github",
        content=json.dumps(payload),
        headers={
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "test-delivery-1",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "event_id" in data
    assert data["status"] == "accepted"


@pytest.mark.asyncio
async def test_github_webhook_valid_signature(client: AsyncClient) -> None:
    """With a secret configured, a valid signature passes."""
    secret = "test-webhook-secret"
    payload = json.dumps({"ref": "refs/heads/main", "repository": {}})
    sig = "sha256=" + hmac.new(
        secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()

    with patch.dict(os.environ, {"REPOWISE_GITHUB_WEBHOOK_SECRET": ""}):
        # Patch the module-level variable
        import repowise.server.routers.webhooks as wh_mod

        original_secret = wh_mod._GITHUB_SECRET
        wh_mod._GITHUB_SECRET = secret
        try:
            resp = await client.post(
                "/api/webhooks/github",
                content=payload,
                headers={
                    "X-GitHub-Event": "push",
                    "X-Hub-Signature-256": sig,
                    "Content-Type": "application/json",
                },
            )
            assert resp.status_code == 200
        finally:
            wh_mod._GITHUB_SECRET = original_secret


@pytest.mark.asyncio
async def test_github_webhook_invalid_signature(client: AsyncClient) -> None:
    """With a secret configured, an invalid signature is rejected."""
    import repowise.server.routers.webhooks as wh_mod

    original_secret = wh_mod._GITHUB_SECRET
    wh_mod._GITHUB_SECRET = "real-secret"
    try:
        resp = await client.post(
            "/api/webhooks/github",
            content=json.dumps({"ref": "refs/heads/main"}),
            headers={
                "X-GitHub-Event": "push",
                "X-Hub-Signature-256": "sha256=invalid",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 401
    finally:
        wh_mod._GITHUB_SECRET = original_secret


@pytest.mark.asyncio
async def test_gitlab_webhook_no_token(client: AsyncClient) -> None:
    """Without a token configured, any payload is accepted."""
    payload = {
        "ref": "refs/heads/main",
        "project": {"web_url": "https://gitlab.com/example/test-repo"},
    }
    resp = await client.post(
        "/api/webhooks/gitlab",
        content=json.dumps(payload),
        headers={
            "X-Gitlab-Event": "Push Hook",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "event_id" in data


@pytest.mark.asyncio
async def test_gitlab_webhook_invalid_token(client: AsyncClient) -> None:
    """With a token configured, wrong token is rejected."""
    import repowise.server.routers.webhooks as wh_mod

    original_token = wh_mod._GITLAB_TOKEN
    wh_mod._GITLAB_TOKEN = "correct-token"
    try:
        resp = await client.post(
            "/api/webhooks/gitlab",
            content=json.dumps({"ref": "refs/heads/main"}),
            headers={
                "X-Gitlab-Event": "Push Hook",
                "X-Gitlab-Token": "wrong-token",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 401
    finally:
        wh_mod._GITLAB_TOKEN = original_token
