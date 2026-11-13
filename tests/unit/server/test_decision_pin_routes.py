"""The decisions page's routes: pin and unpin, the in-force and recent sections, record prompts."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from repowise.core.persistence.crud.authority import accept_decision, set_pinned
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import DecisionRecord
from tests.unit.server.conftest import create_test_repo


async def _seed(app, repo_id: str, *ids: str, pinned: tuple[str, ...] = ()) -> None:
    """In-force decisions *ids*, accepted in that order, with *pinned* pinned."""
    async with get_session(app.state.session_factory) as session:
        for rid in ids:
            rec = DecisionRecord(
                id=rid, repository_id=repo_id, title=f"Rule {rid}", decision=f"do {rid}",
                rationale="why", status="proposed", source="pr",
                affected_files_json=json.dumps(["src/app.py"]),
            )
            session.add(rec)
            await session.flush()
            await accept_decision(
                session, rec, accepter="claude_code", kind="agent", agent_acceptance=True,
                evidence=["abc1234"],
            )
            if rid in pinned:
                await set_pinned(session, rec, True, accepter="Raghav", kind="person")


@pytest.mark.asyncio
async def test_pin_and_unpin_round_trip_as_a_person(client: AsyncClient, app):
    repo = await create_test_repo(client)
    await _seed(app, repo["id"], "d1")
    base = f"/api/repos/{repo['id']}/decisions/d1"

    pinned = await client.post(f"{base}/pin", json={"reason": "law"})
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["pinned"] is True
    # The current authority is still the agent's acceptance.
    assert (pinned.json()["accepter"], pinned.json()["currency"]) == ("claude_code", "active")
    assert (await client.post(f"{base}/pin")).status_code == 400  # already pinned

    unpinned = await client.post(f"{base}/unpin")
    assert unpinned.status_code == 200 and unpinned.json()["pinned"] is False
    assert (await client.get(base)).json()["pinned"] is False


@pytest.mark.asyncio
async def test_pinning_a_background_record_is_refused(client: AsyncClient, app):
    repo = await create_test_repo(client)
    async with get_session(app.state.session_factory) as session:
        session.add(
            DecisionRecord(
                id="bg", repository_id=repo["id"], title="t", decision="d", status="proposed",
                source="pr",
            )
        )
    res = await client.post(f"/api/repos/{repo['id']}/decisions/bg/pin")
    assert res.status_code == 400
    assert "not in force" in res.text


@pytest.mark.asyncio
async def test_in_force_lists_pinned_first_and_counts_them(client: AsyncClient, app):
    repo = await create_test_repo(client)
    await _seed(app, repo["id"], "a1", "b2", "c3", pinned=("c3",))

    rows = (
        await client.get(f"/api/repos/{repo['id']}/decisions", params={"lane": "in_force"})
    ).json()
    assert rows[0]["id"] == "c3" and rows[0]["pinned"] is True
    assert {r["id"] for r in rows[1:]} == {"a1", "b2"}
    assert not any(r["pinned"] for r in rows[1:])

    counts = (await client.get(f"/api/repos/{repo['id']}/decisions/lane-counts")).json()
    assert (counts["in_force"], counts["pinned"], counts["recent"]) == (3, 1, 3)


@pytest.mark.asyncio
async def test_a_pinned_decision_past_the_scan_window_still_leads(
    client: AsyncClient, app, monkeypatch
):
    from repowise.server.routers import decisions as router

    monkeypatch.setattr(router, "_LANE_SCAN_CAP", 1)
    repo = await create_test_repo(client)
    await _seed(app, repo["id"], "a1", "b2", "c3", pinned=("c3",))

    rows = (
        await client.get(
            f"/api/repos/{repo['id']}/decisions", params={"lane": "in_force", "limit": 1}
        )
    ).json()
    assert [r["id"] for r in rows] == ["c3"]


@pytest.mark.asyncio
async def test_recent_rows_say_who_put_them_in_force(client: AsyncClient, app):
    repo = await create_test_repo(client)
    await _seed(app, repo["id"], "a1")

    rows = (
        await client.get(f"/api/repos/{repo['id']}/decisions", params={"lane": "recent"})
    ).json()
    assert [r["id"] for r in rows] == ["a1"]
    recent = rows[0]["recent"]
    assert (recent["signer"], recent["signer_kind"], recent["commit"]) == (
        "claude_code", "agent", "abc1234",
    )


@pytest.mark.asyncio
async def test_a_person_on_the_page_may_retire_a_pinned_decision(client: AsyncClient, app):
    repo = await create_test_repo(client)
    await _seed(app, repo["id"], "d1", pinned=("d1",))

    res = await client.patch(
        f"/api/repos/{repo['id']}/decisions/d1", json={"status": "dismissed"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["currency"] == "dismissed" and res.json()["pinned"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["review", "pin", "unpin", "veto", "edit"])
async def test_record_prompts_are_served_for_one_id(client: AsyncClient, app, kind):
    repo = await create_test_repo(client)
    await _seed(app, repo["id"], "d1")
    url = f"/api/repos/{repo['id']}/decisions/agent-prompt"

    res = await client.get(url, params={"kind": kind, "decision_id": "d1"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["kind"], body["ids"]) == (kind, ["d1"])
    assert "d1" in body["text"] and "Rule d1" in body["text"]

    assert (await client.get(url, params={"kind": kind})).status_code == 400
    missing = await client.get(url, params={"kind": kind, "decision_id": "nope"})
    assert missing.status_code == 404
