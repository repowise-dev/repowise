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
async def test_github_webhook_no_secret_local_accepted(client: AsyncClient) -> None:
    """Without a secret, local callers (loopback) are accepted — dev convenience."""
    payload = {
        "ref": "refs/heads/main",
        "repository": {"clone_url": "https://github.com/example/test-repo.git"},
    }
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("REPOWISE_GITHUB_WEBHOOK_SECRET", None)
        # auth_is_open() is True in unit tests (no API key, loopback host)
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
async def test_github_webhook_no_secret_non_local_rejected(client: AsyncClient) -> None:
    """Without a secret, non-local callers are rejected with 403 (fail-closed)."""
    payload = {
        "ref": "refs/heads/main",
        "repository": {"clone_url": "https://github.com/example/test-repo.git"},
    }
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("REPOWISE_GITHUB_WEBHOOK_SECRET", None)
        with patch("repowise.server.routers.webhooks.auth_is_open", return_value=False):
            resp = await client.post(
                "/api/webhooks/github",
                content=json.dumps(payload),
                headers={
                    "X-GitHub-Event": "push",
                    "X-GitHub-Delivery": "test-delivery-1",
                    "Content-Type": "application/json",
                },
            )
    assert resp.status_code == 403
    assert "REPOWISE_GITHUB_WEBHOOK_SECRET" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_gitlab_webhook_no_token_local_accepted(client: AsyncClient) -> None:
    """Without a token, local callers are accepted — dev convenience."""
    payload = {
        "ref": "refs/heads/main",
        "project": {"web_url": "https://gitlab.com/example/test-repo"},
    }
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("REPOWISE_GITLAB_WEBHOOK_TOKEN", None)
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
async def test_gitlab_webhook_no_token_non_local_rejected(client: AsyncClient) -> None:
    """Without a token, non-local callers are rejected with 403 (fail-closed)."""
    payload = {
        "ref": "refs/heads/main",
        "project": {"web_url": "https://gitlab.com/example/test-repo"},
    }
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("REPOWISE_GITLAB_WEBHOOK_TOKEN", None)
        with patch("repowise.server.routers.webhooks.auth_is_open", return_value=False):
            resp = await client.post(
                "/api/webhooks/gitlab",
                content=json.dumps(payload),
                headers={
                    "X-Gitlab-Event": "Push Hook",
                    "Content-Type": "application/json",
                },
            )
    assert resp.status_code == 403
    assert "REPOWISE_GITLAB_WEBHOOK_TOKEN" in resp.json()["detail"]


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
    """With a secret set in the environment, a valid signature passes."""
    secret = "test-webhook-secret"
    payload = json.dumps({"ref": "refs/heads/main", "repository": {}})
    sig = "sha256=" + hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    with patch.dict(os.environ, {"REPOWISE_GITHUB_WEBHOOK_SECRET": secret}):
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


@pytest.mark.asyncio
async def test_github_webhook_invalid_signature(client: AsyncClient) -> None:
    """With a secret set, an invalid signature is rejected."""
    with patch.dict(os.environ, {"REPOWISE_GITHUB_WEBHOOK_SECRET": "real-secret"}):
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


@pytest.mark.asyncio
async def test_github_webhook_secret_read_per_request(client: AsyncClient) -> None:
    """Secret is read from env per request, not cached at import time."""
    secret = "per-request-secret"
    payload = json.dumps({"ref": "refs/heads/main", "repository": {}})
    sig = "sha256=" + hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    # Set the env var after module import — must still work
    with patch.dict(os.environ, {"REPOWISE_GITHUB_WEBHOOK_SECRET": secret}):
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


@pytest.mark.asyncio
async def test_gitlab_webhook_invalid_token(client: AsyncClient) -> None:
    """With a token set, wrong token is rejected."""
    with patch.dict(os.environ, {"REPOWISE_GITLAB_WEBHOOK_TOKEN": "correct-token"}):
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


@pytest.mark.asyncio
async def test_gitlab_webhook_token_read_per_request(client: AsyncClient) -> None:
    """Token is read from env per request, not cached at import time."""
    token = "per-request-token"

    with patch.dict(os.environ, {"REPOWISE_GITLAB_WEBHOOK_TOKEN": token}):
        resp = await client.post(
            "/api/webhooks/gitlab",
            content=json.dumps({"ref": "refs/heads/main", "project": {}}),
            headers={
                "X-Gitlab-Event": "Push Hook",
                "X-Gitlab-Token": token,
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 200
