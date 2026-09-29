"""/api/repos/{repo_id}/actions: the response shape and the state round trip."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import SecurityFinding
from repowise.server.routers import actions
from repowise.server.schemas.actions import ActionsResponse
from tests.unit.server.conftest import create_test_repo


@pytest.fixture
async def actions_client(app, client: AsyncClient) -> AsyncClient:
    app.include_router(actions.router)
    return client


async def _seed_secret(session_factory, repo_id: str) -> None:
    async with get_session(session_factory) as session:
        session.add(
            SecurityFinding(
                repository_id=repo_id,
                file_path="src/settings.py",
                kind="hardcoded_secret",
                severity="high",
                snippet='TOKEN = "sk_live_123"',
                line_number=4,
                commit_sha="",
            )
        )


@pytest.mark.anyio
async def test_get_returns_the_actions_shape(actions_client: AsyncClient, session_factory) -> None:
    repo = await create_test_repo(actions_client)
    await _seed_secret(session_factory, repo["id"])

    resp = await actions_client.get(f"/api/repos/{repo['id']}/actions")
    assert resp.status_code == 200
    body = ActionsResponse.model_validate(resp.json())
    assert set(body.horizons) == {"week", "quarter"}
    (secret,) = [a for a in body.horizons["quarter"].actions if a.rule == "live_secret"]
    assert secret.tier == "act_now"
    assert len(body.rules) == 12


@pytest.mark.anyio
async def test_put_state_hides_the_action(actions_client: AsyncClient, session_factory) -> None:
    repo = await create_test_repo(actions_client)
    await _seed_secret(session_factory, repo["id"])
    url = f"/api/repos/{repo['id']}/actions"
    secret = next(
        a
        for a in (await actions_client.get(url)).json()["horizons"]["quarter"]["actions"]
        if a["rule"] == "live_secret"
    )

    resp = await actions_client.put(
        f"{url}/{secret['id']}/state",
        json={"state": "dismissed", "fingerprint": secret["fingerprint"]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"action_id": secret["id"], "state": "dismissed", "until": None}

    quarter = (await actions_client.get(url)).json()["horizons"]["quarter"]
    assert quarter["hidden"] == 1
    assert all(a["id"] != secret["id"] for a in quarter["actions"])

    snoozed = await actions_client.put(f"{url}/{secret['id']}/state", json={"state": "snoozed"})
    assert snoozed.json()["until"] is not None
    assert (await actions_client.get(url)).json()["horizons"]["quarter"]["hidden"] == 1

    cleared = await actions_client.put(f"{url}/{secret['id']}/state", json={"state": None})
    assert cleared.status_code == 200
    assert (await actions_client.get(url)).json()["horizons"]["quarter"]["hidden"] == 0
