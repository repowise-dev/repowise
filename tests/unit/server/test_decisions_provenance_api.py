"""The API says who signed, so a dashboard can show it.

A decision list that carries ``currency`` but not the signature can say that
something governs and not whether a person granted it, which is the whole gap
the provenance columns close.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from tests.unit.server.conftest import create_test_repo


async def _accepted(session_factory, repo_id: str, *, title: str, **accept) -> str:
    async with get_session(session_factory) as session:
        rec = await crud.upsert_decision(
            session,
            repository_id=repo_id,
            title=title,
            status="proposed",
            context="ctx",
            decision=f"dec for {title}",
            rationale="why",
            source="inline_marker",
            affected_files=["src/app.py"],
            evidence_file="src/app.py",
            confidence=0.5,
        )
        await crud.accept_decision(session, rec, evidence=["seed"], **accept)
        await session.flush()
        return rec.id


@pytest.mark.asyncio
async def test_a_list_row_carries_who_signed(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _accepted(
        app.state.session_factory,
        repo["id"],
        title="Signed by an agent",
        accepter="claude_code",
        kind="agent",
        accepter_session="sess-42",
        agent_acceptance=True,
    )

    rows = (await client.get(f"/api/repos/{repo['id']}/decisions")).json()

    assert [r["accepter_kind"] for r in rows] == ["agent"]
    assert rows[0]["accepter"] == "claude_code"
    assert rows[0]["accepter_session"] == "sess-42"


@pytest.mark.asyncio
async def test_a_candidate_carries_no_signature(client: AsyncClient, app) -> None:
    """Null, not blank: an unsigned row must not render as signed."""
    repo = await create_test_repo(client)
    async with get_session(app.state.session_factory) as session:
        await crud.upsert_decision(
            session,
            repository_id=repo["id"],
            title="Nobody accepted this",
            status="proposed",
            context="ctx",
            decision="dec",
            rationale="why",
            source="inline_marker",
            affected_files=["src/app.py"],
            evidence_file="src/app.py",
            confidence=0.5,
        )

    rows = (await client.get(f"/api/repos/{repo['id']}/decisions")).json()

    assert rows[0]["currency"] is None
    assert rows[0]["accepter_kind"] is None
    assert rows[0]["accepter"] is None


@pytest.mark.asyncio
async def test_the_detail_route_says_who_signed_too(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    decision_id = await _accepted(
        app.state.session_factory, repo["id"], title="A rule", accepter="Raghav", kind="person"
    )

    row = (await client.get(f"/api/repos/{repo['id']}/decisions/{decision_id}")).json()

    assert (row["accepter"], row["accepter_kind"]) == ("Raghav", "person")


@pytest.mark.asyncio
async def test_the_web_route_signs_as_a_person(client: AsyncClient) -> None:
    """The person at the keyboard is the signer, and the row says so."""
    repo = await create_test_repo(client)

    created = await client.post(
        f"/api/repos/{repo['id']}/decisions",
        json={
            "title": "Typed by hand",
            "decision": "do the thing",
            "rationale": "because",
            "affected_files": ["src/app.py"],
        },
    )

    assert created.status_code == 201, created.text
    assert created.json()["accepter_kind"] == "person"


@pytest.mark.asyncio
async def test_the_create_route_can_record_an_agreement(client: AsyncClient) -> None:
    """An agreement names no file because it governs the repository.

    Without ``kind`` on the wire this route could only build the checkable
    noun, so an agreement posted here was stored as an unacceptable
    architectural record naming nothing.
    """
    repo = await create_test_repo(client)

    created = await client.post(
        f"/api/repos/{repo['id']}/decisions",
        json={
            "title": "Commit on a branch, never on main",
            "kind": "agreement",
            "decision": "branch first",
            "rationale": "main is protected",
        },
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["kind"] == "agreement"
    assert body["accepter_kind"] == "person", "an agreement must be acceptable"


@pytest.mark.asyncio
async def test_settings_report_the_agent_grant_and_can_change_it(
    client: AsyncClient,
) -> None:
    repo = await create_test_repo(client)

    before = (await client.get(f"/api/repos/{repo['id']}/decisions/settings")).json()
    assert before["agent_acceptance"] is False

    after = await client.put(
        f"/api/repos/{repo['id']}/decisions/settings",
        json={"agent_acceptance": True, "etag": before["etag"]},
    )
    assert after.status_code == 200, after.text
    assert after.json()["agent_acceptance"] is True

    reread = (await client.get(f"/api/repos/{repo['id']}/decisions/settings")).json()
    assert reread["agent_acceptance"] is True


@pytest.mark.asyncio
async def test_a_capture_preset_does_not_revoke_the_agent_grant(
    client: AsyncClient,
) -> None:
    """A preset names source membership. Authority is not membership."""
    repo = await create_test_repo(client)
    first = (await client.get(f"/api/repos/{repo['id']}/decisions/settings")).json()
    on = await client.put(
        f"/api/repos/{repo['id']}/decisions/settings",
        json={"agent_acceptance": True, "etag": first["etag"]},
    )

    after = await client.put(
        f"/api/repos/{repo['id']}/decisions/settings",
        json={"preset": "balanced", "etag": on.json()["etag"]},
    )

    assert after.status_code == 200, after.text
    assert after.json()["agent_acceptance"] is True


@pytest.mark.asyncio
async def test_an_agreement_body_cannot_clear_a_governed_decision(
    client: AsyncClient,
) -> None:
    """The 409 asks what the record would lose, not what the body claims.

    ``kind: agreement`` says "this names no file because it governs the
    repository". Letting that satisfy the guard would let it be said *about a
    record that does govern files*, whose scope the restate then clears while
    its acceptance row survives.
    """
    repo = await create_test_repo(client)
    first = await client.post(
        f"/api/repos/{repo['id']}/decisions",
        json={
            "title": "Use Postgres",
            "decision": "postgres",
            "rationale": "because",
            "affected_files": ["db/engine.py"],
        },
    )
    assert first.json()["accepter_kind"] == "person"

    clash = await client.post(
        f"/api/repos/{repo['id']}/decisions",
        json={"title": "Use Postgres", "kind": "agreement", "decision": "x", "rationale": "y"},
    )

    assert clash.status_code == 409, clash.text
    still = (
        await client.get(f"/api/repos/{repo['id']}/decisions/{first.json()['id']}")
    ).json()
    assert still["affected_files"] == ["db/engine.py"]
    assert still["kind"] == "architectural"


@pytest.mark.asyncio
async def test_an_agreement_that_names_files_keeps_them(client: AsyncClient) -> None:
    """The noun does not say whether there is a scope to lose.

    An agreement can be given a real one, and then the ordinary rules apply to
    it. Exempting the guard by kind let a re-post silently clear it.
    """
    repo = await create_test_repo(client)
    created = await client.post(
        f"/api/repos/{repo['id']}/decisions",
        json={
            "title": "Release checklist",
            "kind": "agreement",
            "decision": "follow it",
            "rationale": "releases went out unchecked",
            "affected_files": ["docs/RELEASING.md"],
        },
    )
    assert created.status_code == 201, created.text

    clash = await client.post(
        f"/api/repos/{repo['id']}/decisions",
        json={"title": "Release checklist", "decision": "follow it", "rationale": "typo"},
    )

    assert clash.status_code == 409, clash.text
    still = (
        await client.get(f"/api/repos/{repo['id']}/decisions/{created.json()['id']}")
    ).json()
    assert still["affected_files"] == ["docs/RELEASING.md"]


@pytest.mark.asyncio
async def test_an_agreement_naming_nothing_is_still_restatable(client: AsyncClient) -> None:
    """The guard must not fire where there is genuinely nothing to clear."""
    repo = await create_test_repo(client)
    await client.post(
        f"/api/repos/{repo['id']}/decisions",
        json={
            "title": "Commit on a branch",
            "kind": "agreement",
            "decision": "branch first",
            "rationale": "main is protected",
        },
    )

    again = await client.post(
        f"/api/repos/{repo['id']}/decisions",
        json={"title": "Commit on a branch", "decision": "branch first", "rationale": "clearer"},
    )

    assert again.status_code == 201, again.text
    assert again.json()["rationale"] == "clearer"


@pytest.mark.asyncio
async def test_a_body_naming_no_kind_leaves_a_stored_agreement_alone(
    client: AsyncClient,
) -> None:
    """A client that predates the split must not silently un-agree one."""
    repo = await create_test_repo(client)
    created = await client.post(
        f"/api/repos/{repo['id']}/decisions",
        json={
            "title": "Commit on a branch",
            "kind": "agreement",
            "decision": "branch first",
            "rationale": "main is protected",
        },
    )
    assert created.json()["kind"] == "agreement"

    restated = await client.post(
        f"/api/repos/{repo['id']}/decisions",
        json={"title": "Commit on a branch", "decision": "branch first", "rationale": "fixed typo"},
    )

    assert restated.status_code == 201, restated.text
    assert restated.json()["kind"] == "agreement"


@pytest.mark.asyncio
async def test_a_detail_row_carries_its_currency_beside_its_signature(
    client: AsyncClient, app
) -> None:
    """Both or neither: a consumer reading a null currency as "candidate" would
    otherwise call an accepted decision one and then find a signature on it."""
    repo = await create_test_repo(client)
    decision_id = await _accepted(
        app.state.session_factory, repo["id"], title="A rule", accepter="Raghav", kind="person"
    )

    row = (await client.get(f"/api/repos/{repo['id']}/decisions/{decision_id}")).json()

    assert row["currency"] == "active"
    assert row["accepter_kind"] == "person"
