"""An id that outlived the row it named still reaches the right decision.

Decisions move onto derived ids, which retires the id anything outside the
store wrote down, so the id-taking routes resolve through the alias table. The
resolution has to be narrow: a live record always answers about itself, or a
merged candidate sitting in the review lane would answer as the decision it was
folded into, and a PATCH aimed at the candidate would land on that decision.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import DecisionAlias
from tests.unit.server.conftest import create_test_repo


async def _record(session_factory, repo_id: str, title: str) -> str:
    async with get_session(session_factory) as session:
        rec = await crud.upsert_decision(
            session,
            repository_id=repo_id,
            title=title,
            status="proposed",
            context="ctx",
            decision="dec",
            rationale="why",
            source="inline_marker",
            affected_files=["src/app.py"],
        )
        await session.commit()
        return rec.id


@pytest.mark.asyncio
async def test_a_retired_id_still_reaches_its_decision(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    live = await _record(sf, repo["id"], "Prefer the boring option")
    retired = "f" * 32
    async with get_session(sf) as session:
        session.add(
            DecisionAlias(
                alias_id=retired,
                repository_id=repo["id"],
                decision_id=live,
                reason="rekeyed",
            )
        )
        await session.commit()

    resp = await client.get(f"/api/repos/{repo['id']}/decisions/{retired}")

    assert resp.status_code == 200
    assert resp.json()["id"] == live
    assert resp.json()["title"] == "Prefer the boring option"


@pytest.mark.asyncio
async def test_a_merged_candidate_still_answers_about_itself(
    client: AsyncClient, app
) -> None:
    """A merge keeps the candidate's row, and the review lane still lists it.

    Following the merge here would answer a click on the candidate with a
    different decision's title and evidence.
    """
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    folded = await _record(sf, repo["id"], "Folded away")
    target = await _record(sf, repo["id"], "The survivor")
    async with get_session(sf) as session:
        session.add(
            DecisionAlias(
                alias_id=folded,
                repository_id=repo["id"],
                decision_id=target,
                reason="merged",
            )
        )
        await session.commit()

    resp = await client.get(f"/api/repos/{repo['id']}/decisions/{folded}")

    assert resp.status_code == 200
    assert resp.json()["id"] == folded
    assert resp.json()["title"] == "Folded away"


@pytest.mark.asyncio
async def test_a_patch_lands_on_the_record_it_named(client: AsyncClient, app) -> None:
    """The dangerous case: dismissing a leftover candidate from the lane.

    If the merge were followed, this would withdraw the decision it was folded
    into instead.
    """
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    folded = await _record(sf, repo["id"], "Folded away")
    target = await _record(sf, repo["id"], "The survivor")
    async with get_session(sf) as session:
        session.add(
            DecisionAlias(
                alias_id=folded,
                repository_id=repo["id"],
                decision_id=target,
                reason="merged",
            )
        )
        await session.commit()

    resp = await client.patch(
        f"/api/repos/{repo['id']}/decisions/{folded}",
        json={"status": "dismissed"},
    )

    assert resp.status_code == 200
    assert resp.json()["id"] == folded
    survivor = await client.get(f"/api/repos/{repo['id']}/decisions/{target}")
    assert survivor.json()["status"] != "dismissed"
