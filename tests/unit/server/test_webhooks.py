"""Tests for /api/webhooks endpoints."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from repowise.core.persistence.models import GenerationJob, Repository


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


@pytest.mark.parametrize(
    ("endpoint", "headers", "repository_payload"),
    [
        (
            "/api/webhooks/github",
            {
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "test-delivery-sync",
            },
            {"repository": {"clone_url": "https://example.com/test-repo"}},
        ),
        (
            "/api/webhooks/gitlab",
            {"X-Gitlab-Event": "Push Hook"},
            {"project": {"web_url": "https://example.com/test-repo"}},
        ),
    ],
)
@pytest.mark.asyncio
async def test_push_webhook_enqueues_sync_job(
    client: AsyncClient,
    session_factory,
    endpoint: str,
    headers: dict[str, str],
    repository_payload: dict[str, object],
) -> None:
    """Push webhooks use the executor's sync mode so docs are regenerated."""
    async with session_factory() as session:
        session.add(
            Repository(
                name="test-repo",
                url="https://example.com/test-repo",
                local_path="/tmp/test-repo",
                default_branch="main",
            )
        )
        await session.commit()

    payload = {
        "ref": "refs/heads/main",
        "before": "before-sha",
        "after": "after-sha",
        **repository_payload,
    }
    with patch("repowise.server.routers.webhooks._launch_webhook_job"):
        response = await client.post(
            endpoint,
            content=json.dumps(payload),
            headers={**headers, "Content-Type": "application/json"},
        )

    assert response.status_code == 200
    async with session_factory() as session:
        job = (await session.execute(select(GenerationJob))).scalar_one()
        config = json.loads(job.config_json)

    assert config["mode"] == "sync"
    assert config["trigger"] == "webhook"
    assert config["before"] == "before-sha"
    assert config["after"] == "after-sha"


@pytest.mark.asyncio
async def test_github_webhook_valid_signature(client: AsyncClient) -> None:
    """With a secret configured, a valid signature passes."""
    secret = "test-webhook-secret"
    payload = json.dumps({"ref": "refs/heads/main", "repository": {}})
    sig = "sha256=" + hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

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
