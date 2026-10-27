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

from repowise.core.persistence.models import GenerationJob, Repository, WebhookEvent


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
        (
            "/api/webhooks/gitea",
            {
                "X-Gitea-Event": "push",
                "X-Gitea-Delivery": "test-delivery-sync",
            },
            {"repository": {"clone_url": "https://example.com/test-repo"}},
        ),
        (
            "/api/webhooks/gitea",
            # Forgejo sends both header families; the Gitea names are what a
            # pre-Forgejo integration would have matched on.
            {
                "X-Forgejo-Event": "push",
                "X-Gitea-Event": "push",
                "X-Forgejo-Delivery": "test-delivery-sync",
            },
            {"repository": {"clone_url": "https://example.com/test-repo"}},
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


def _gitea_payload() -> str:
    return json.dumps(
        {
            "ref": "refs/heads/main",
            "repository": {"clone_url": "https://gitea.example.com/example/test-repo.git"},
        }
    )


@pytest.mark.asyncio
async def test_gitea_webhook_no_secret_local_accepted(client: AsyncClient) -> None:
    """Without a secret, local callers (loopback) are accepted — dev convenience."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("REPOWISE_GITEA_WEBHOOK_SECRET", None)
        resp = await client.post(
            "/api/webhooks/gitea",
            content=_gitea_payload(),
            headers={
                "X-Gitea-Event": "push",
                "X-Gitea-Delivery": "test-delivery-gitea",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


@pytest.mark.asyncio
async def test_gitea_webhook_no_secret_non_local_rejected(client: AsyncClient) -> None:
    """Without a secret, non-local callers are rejected with 403 (fail-closed)."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("REPOWISE_GITEA_WEBHOOK_SECRET", None)
        with patch("repowise.server.routers.webhooks.auth_is_open", return_value=False):
            resp = await client.post(
                "/api/webhooks/gitea",
                content=_gitea_payload(),
                headers={
                    "X-Gitea-Event": "push",
                    "Content-Type": "application/json",
                },
            )
    assert resp.status_code == 403
    assert "REPOWISE_GITEA_WEBHOOK_SECRET" in resp.json()["detail"]


@pytest.mark.parametrize("header", ["X-Gitea-Signature", "X-Forgejo-Signature"])
@pytest.mark.asyncio
async def test_gitea_webhook_valid_signature(client: AsyncClient, header: str) -> None:
    """A bare lowercase hex HMAC-SHA256 digest passes, under either header name."""
    secret = "test-webhook-secret"
    body = _gitea_payload()
    digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()

    with patch.dict(os.environ, {"REPOWISE_GITEA_WEBHOOK_SECRET": secret}):
        resp = await client.post(
            "/api/webhooks/gitea",
            content=body,
            headers={
                "X-Gitea-Event": "push",
                header: digest,
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_gitea_webhook_invalid_signature(client: AsyncClient) -> None:
    """A digest computed with the wrong secret is rejected."""
    with patch.dict(os.environ, {"REPOWISE_GITEA_WEBHOOK_SECRET": "real-secret"}):
        body = _gitea_payload()
        wrong = hmac.new(b"wrong-secret", body.encode(), hashlib.sha256).hexdigest()
        resp = await client.post(
            "/api/webhooks/gitea",
            content=body,
            headers={
                "X-Gitea-Event": "push",
                "X-Gitea-Signature": wrong,
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_gitea_webhook_rejects_github_prefixed_signature(client: AsyncClient) -> None:
    """The GitHub ``sha256=`` form is not Gitea's: this route takes bare hex only.

    Gitea sends the prefixed digest too, as ``X-Hub-Signature-256``, and the
    GitHub route reads that one. Accepting the prefix here would make the
    endpoint take two grammars for the same secret.
    """
    secret = "test-webhook-secret"
    body = _gitea_payload()
    digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()

    with patch.dict(os.environ, {"REPOWISE_GITEA_WEBHOOK_SECRET": secret}):
        resp = await client.post(
            "/api/webhooks/gitea",
            content=body,
            headers={
                "X-Gitea-Event": "push",
                "X-Gitea-Signature": f"sha256={digest}",
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_gitea_webhook_missing_signature_rejected(client: AsyncClient) -> None:
    """With a secret set, a delivery carrying no signature is rejected."""
    with patch.dict(os.environ, {"REPOWISE_GITEA_WEBHOOK_SECRET": "real-secret"}):
        resp = await client.post(
            "/api/webhooks/gitea",
            content=_gitea_payload(),
            headers={"X-Gitea-Event": "push", "Content-Type": "application/json"},
        )
    assert resp.status_code == 401


@pytest.mark.parametrize(
    ("headers", "expected_provider"),
    [
        ({"X-Gitea-Event": "push"}, "gitea"),
        ({"X-Gitea-Event": "push", "X-Forgejo-Event": "push"}, "forgejo"),
    ],
)
@pytest.mark.asyncio
async def test_gitea_webhook_records_sending_forge(
    client: AsyncClient,
    session_factory,
    headers: dict[str, str],
    expected_provider: str,
) -> None:
    """Forgejo identifies itself, so the event is not recorded as Gitea."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("REPOWISE_GITEA_WEBHOOK_SECRET", None)
        resp = await client.post(
            "/api/webhooks/gitea",
            content=_gitea_payload(),
            headers={**headers, "Content-Type": "application/json"},
        )
    assert resp.status_code == 200

    async with session_factory() as session:
        event = (await session.execute(select(WebhookEvent))).scalar_one()
    assert event.provider == expected_provider


@pytest.mark.asyncio
async def test_push_webhook_does_not_match_url_prefix_substring(
    client: AsyncClient,
    session_factory,
) -> None:
    """A short forged clone URL must not hit a longer registered repo URL.

    Regression for #1392: ``Repository.url.contains(repo_url[:50])`` let
    ``https://github.com/org/re`` match ``https://github.com/org/repowise``.
    """
    async with session_factory() as session:
        session.add(
            Repository(
                name="repowise",
                url="https://github.com/org/repowise.git",
                local_path="/tmp/repowise",
                default_branch="main",
            )
        )
        await session.commit()

    payload = {
        "ref": "refs/heads/main",
        "before": "before-sha",
        "after": "after-sha",
        "repository": {"clone_url": "https://github.com/org/re"},
    }
    with patch("repowise.server.routers.webhooks._launch_webhook_job") as launch:
        response = await client.post(
            "/api/webhooks/github",
            content=json.dumps(payload),
            headers={
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "prefix-attack",
                "Content-Type": "application/json",
            },
        )

    assert response.status_code == 200
    launch.assert_not_called()
    async with session_factory() as session:
        jobs = (await session.execute(select(GenerationJob))).scalars().all()
    assert jobs == []


@pytest.mark.asyncio
async def test_push_webhook_matches_clone_url_without_git_suffix(
    client: AsyncClient,
    session_factory,
) -> None:
    """Stored ``…/repo`` still matches a GitHub ``…/repo.git`` clone_url."""
    async with session_factory() as session:
        session.add(
            Repository(
                name="test-repo",
                url="https://github.com/example/test-repo",
                local_path="/tmp/test-repo",
                default_branch="main",
            )
        )
        await session.commit()

    payload = {
        "ref": "refs/heads/main",
        "before": "b",
        "after": "a",
        "repository": {"clone_url": "https://github.com/example/test-repo.git"},
    }
    with patch("repowise.server.routers.webhooks._launch_webhook_job"):
        response = await client.post(
            "/api/webhooks/github",
            content=json.dumps(payload),
            headers={
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "suffix-ok",
                "Content-Type": "application/json",
            },
        )

    assert response.status_code == 200
    async with session_factory() as session:
        job = (await session.execute(select(GenerationJob))).scalar_one()
        config = json.loads(job.config_json)
    assert config["trigger"] == "webhook"


@pytest.mark.asyncio
async def test_push_webhook_matches_mixed_case_path(
    client: AsyncClient,
    session_factory,
) -> None:
    """Registered URL and webhook clone_url may differ only in path casing.

    GitHub/GitLab resolve paths case-insensitively; SQLite ``contains`` did
    too. A case-sensitive normalizer would return 200 without enqueueing.
    """
    async with session_factory() as session:
        session.add(
            Repository(
                name="RepoWise",
                url="https://github.com/org/RepoWise",
                local_path="/tmp/RepoWise",
                default_branch="main",
            )
        )
        await session.commit()

    payload = {
        "ref": "refs/heads/main",
        "before": "b",
        "after": "a",
        "repository": {"clone_url": "https://github.com/org/repowise.git"},
    }
    with patch("repowise.server.routers.webhooks._launch_webhook_job"):
        response = await client.post(
            "/api/webhooks/github",
            content=json.dumps(payload),
            headers={
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "case-ok",
                "Content-Type": "application/json",
            },
        )

    assert response.status_code == 200
    async with session_factory() as session:
        job = (await session.execute(select(GenerationJob))).scalar_one()
        config = json.loads(job.config_json)
    assert config["trigger"] == "webhook"


def test_normalize_scm_url_variants() -> None:
    from repowise.server.routers.webhooks import _normalize_scm_url

    assert _normalize_scm_url("https://github.com/Org/Repo.git") == "github.com/org/repo"
    assert _normalize_scm_url("https://github.com/Org/Repo/") == "github.com/org/repo"
    assert _normalize_scm_url("git@github.com:Org/Repo.git") == "github.com/org/repo"
    assert _normalize_scm_url("https://github.com/org/RepoWise") == (
        _normalize_scm_url("https://github.com/org/repowise.git")
    )
    assert _normalize_scm_url("") == ""
    assert _normalize_scm_url("/srv/git/repo.git") == ""


def test_normalize_scm_url_folds_azure_shapes_to_one_key() -> None:
    """An Azure push names the repo by one URL shape, the stored repo may use another."""
    from repowise.server.routers.webhooks import _normalize_scm_url

    key = "dev.azure.com/org/my%20project/_git/repo"
    for url in (
        "https://org@dev.azure.com/org/My%20Project/_git/repo",
        "git@ssh.dev.azure.com:v3/org/My%20Project/repo",
        "https://org.visualstudio.com/DefaultCollection/My Project/_git/repo",
    ):
        assert _normalize_scm_url(url) == key


def test_normalize_scm_url_drops_gitlab_web_routes() -> None:
    from repowise.server.routers.webhooks import _normalize_scm_url

    assert _normalize_scm_url("https://gitlab.com/G/Sub/P/-/tree/main") == "gitlab.com/g/sub/p"
