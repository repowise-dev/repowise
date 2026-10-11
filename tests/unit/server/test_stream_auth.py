"""SSE job-stream authentication (D5) and the per-job stream token.

An ``EventSource`` cannot send the Authorization header, so the job progress
stream authenticates with a short-lived, single-job-scoped ``?token=`` minted at
launch (and re-minted on every authenticated job read). These tests cover the
token itself, the launch responses carrying it, ``JobResponse.stream_token``, and
the stream endpoint accepting either a bearer key or a valid token while
rejecting a missing or cross-job one.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.server import deps, stream_auth
from tests.unit.server.conftest import create_test_repo

# ---------------------------------------------------------------------------
# The token primitive
# ---------------------------------------------------------------------------


def test_token_round_trips_for_its_job() -> None:
    token = stream_auth.mint_stream_token("job-1")
    assert stream_auth.verify_stream_token(token, "job-1") is True


def test_token_is_not_replayable_against_another_job() -> None:
    token = stream_auth.mint_stream_token("job-1")
    assert stream_auth.verify_stream_token(token, "job-2") is False


def test_tampered_or_malformed_token_rejected() -> None:
    token = stream_auth.mint_stream_token("job-1")
    assert stream_auth.verify_stream_token(token + "x", "job-1") is False
    assert stream_auth.verify_stream_token("not-a-token", "job-1") is False
    assert stream_auth.verify_stream_token("", "job-1") is False
    assert stream_auth.verify_stream_token(None, "job-1") is False


def test_expired_token_rejected() -> None:
    # A token minted in a bucket already in the past has an expiry <= now.
    token = stream_auth.mint_stream_token("job-1", ttl_seconds=1)
    # Its expiry is (bucket+2)*1 seconds; wait past it deterministically by
    # crafting an equivalent expired payload rather than sleeping.
    import base64
    import hmac
    import time
    from hashlib import sha256

    payload = f"{int(time.time()) - 5}:job-1"
    sig = hmac.new(stream_auth._signing_secret(), payload.encode(), sha256).digest()
    expired = (
        base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()
        + "."
        + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    )
    assert stream_auth.verify_stream_token(expired, "job-1") is False
    # (the freshly-minted one is still valid)
    assert stream_auth.verify_stream_token(token, "job-1") is True


def test_token_is_stable_within_a_bucket() -> None:
    # Re-minting for the same job returns the same string, so the browser's
    # EventSource URL doesn't churn on every poll.
    assert stream_auth.mint_stream_token("job-1") == stream_auth.mint_stream_token("job-1")


# ---------------------------------------------------------------------------
# Launch responses + JobResponse carry the token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_response_carries_stream_token(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    from unittest.mock import patch

    with patch("repowise.server.routers.repos._launch_job_task"):
        resp = await client.post(f"/api/repos/{repo['id']}/sync")
    body = resp.json()
    assert body["job_id"]
    assert stream_auth.verify_stream_token(body["stream_token"], body["job_id"]) is True


@pytest.mark.asyncio
async def test_index_launch_response_carries_stream_token(client: AsyncClient, app) -> None:
    # Regression: the /index endpoint builds the accepted payload from `job_id`
    # (a str), not a job object; a wrong `job.id` there NameErrors into a 500.
    from unittest.mock import AsyncMock, patch

    repo = await create_test_repo(client)
    with (
        patch(
            "repowise.server.repo_db.ensure_repo_registration",
            AsyncMock(return_value=(app.state.session_factory, repo["id"])),
        ),
        patch("repowise.server.routers.repos._launch_job_task"),
    ):
        resp = await client.post(f"/api/repos/{repo['id']}/index")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert stream_auth.verify_stream_token(body["stream_token"], body["job_id"]) is True


@pytest.mark.asyncio
async def test_job_response_has_token_while_active_and_not_when_terminal(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    async with app.state.session_factory() as session:
        active = await crud.upsert_generation_job(
            session, repository_id=repo["id"], status="running", config={"mode": "sync"}
        )
        await session.commit()
        active_id = active.id

    resp = await client.get(f"/api/jobs/{active_id}")
    assert resp.status_code == 200
    token = resp.json()["stream_token"]
    assert stream_auth.verify_stream_token(token, active_id) is True

    # Flip to terminal: no token to hand out (nothing left to stream).
    async with app.state.session_factory() as session:
        await crud.update_job_status(session, active_id, "completed")
        await session.commit()

    resp = await client.get(f"/api/jobs/{active_id}")
    assert resp.json()["stream_token"] is None


# ---------------------------------------------------------------------------
# The stream endpoint: bearer OR token, and nothing else
# ---------------------------------------------------------------------------


_TEST_KEY = "test-secret-key"


def _enable_api_key(monkeypatch) -> str:
    """Turn on server auth AFTER unauthenticated setup (repo/job) is done.

    ``verify_api_key`` captured ``_API_KEY`` at import, so patch the module
    attribute; ``stream_auth`` signs from the live env, so set that too, keeping
    signing and verification consistent.
    """
    monkeypatch.setattr(deps, "_API_KEY", _TEST_KEY)
    monkeypatch.setattr(deps, "_REPOWISE_HOST", "127.0.0.1")
    monkeypatch.setenv("REPOWISE_API_KEY", _TEST_KEY)
    return _TEST_KEY


async def _completed_job(app, repo_id: str) -> str:
    """A terminal job so the stream generator returns immediately."""
    async with app.state.session_factory() as session:
        job = await crud.upsert_generation_job(
            session, repository_id=repo_id, status="completed", config={"mode": "sync"}
        )
        await session.commit()
        return job.id


@pytest.mark.asyncio
async def test_stream_open_without_key_needs_no_token(client: AsyncClient, app) -> None:
    # Default fixtures: no REPOWISE_API_KEY, loopback bind -> open access.
    repo = await create_test_repo(client)
    job_id = await _completed_job(app, repo["id"])
    resp = await client.get(f"/api/jobs/{job_id}/stream")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_stream_rejected_without_credentials(client: AsyncClient, app, monkeypatch) -> None:
    repo = await create_test_repo(client)
    job_id = await _completed_job(app, repo["id"])
    _enable_api_key(monkeypatch)
    resp = await client.get(f"/api/jobs/{job_id}/stream")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_stream_accepts_valid_token(client: AsyncClient, app, monkeypatch) -> None:
    repo = await create_test_repo(client)
    job_id = await _completed_job(app, repo["id"])
    _enable_api_key(monkeypatch)
    token = stream_auth.mint_stream_token(job_id)
    resp = await client.get(f"/api/jobs/{job_id}/stream?token={token}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_stream_rejects_token_for_another_job(client: AsyncClient, app, monkeypatch) -> None:
    repo = await create_test_repo(client)
    job_id = await _completed_job(app, repo["id"])
    other = await _completed_job(app, repo["id"])
    _enable_api_key(monkeypatch)
    token_for_other = stream_auth.mint_stream_token(other)
    resp = await client.get(f"/api/jobs/{job_id}/stream?token={token_for_other}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_stream_accepts_bearer_key(client: AsyncClient, app, monkeypatch) -> None:
    repo = await create_test_repo(client)
    job_id = await _completed_job(app, repo["id"])
    key = _enable_api_key(monkeypatch)
    resp = await client.get(
        f"/api/jobs/{job_id}/stream",
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_other_job_routes_still_require_bearer(client: AsyncClient, app, monkeypatch) -> None:
    # A stream token must not open the non-stream job routes.
    repo = await create_test_repo(client)
    job_id = await _completed_job(app, repo["id"])
    key = _enable_api_key(monkeypatch)
    token = stream_auth.mint_stream_token(job_id)
    resp = await client.get(f"/api/jobs/{job_id}?token={token}")
    assert resp.status_code == 401
    resp = await client.get(f"/api/jobs/{job_id}", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
