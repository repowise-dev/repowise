"""The acceptance contract's reads of a record, pinned at the server.

The reason falls back from ``rationale`` to ``context`` and never to
``decision``, and an agreement naming no file governs the repository.
"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from repowise.core.persistence import crud
from repowise.core.persistence.crud.authority import candidate_review_signals
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import DecisionAcceptance
from tests.unit.server.conftest import create_test_repo


async def _create(client: AsyncClient, repo_id: str, **body) -> dict:
    res = await client.post(f"/api/repos/{repo_id}/decisions", json=body)
    assert res.status_code == 201, res.text
    return res.json()


@pytest.mark.asyncio
async def test_reason_falls_back_to_context_and_never_to_decision(
    client: AsyncClient,
) -> None:
    repo = await create_test_repo(client)
    from_context = await _create(
        client,
        repo["id"],
        title="Context gives the why",
        context="the cache thrashed",
        decision="use an LRU",
        rationale="",
        affected_files=["src/cache.py"],
    )
    from_decision = await _create(
        client,
        repo["id"],
        title="Only the what is stated",
        context="",
        decision="use an LRU",
        rationale="",
        affected_files=["src/cache.py"],
    )
    assert from_context["status"] == "active"
    assert from_decision["status"] == "proposed"


@pytest.mark.asyncio
async def test_a_file_less_agreement_governs_the_repository(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    agreement = await _create(
        client,
        repo["id"],
        title="Commit on feature branches",
        kind="agreement",
        rationale="main is protected",
    )
    architectural = await _create(
        client,
        repo["id"],
        title="No scope named",
        rationale="because",
    )
    assert agreement["status"] == "active"
    assert agreement["currency"] == "active"
    assert architectural["status"] == "proposed"

    async with get_session(app.state.session_factory) as session:
        row = (
            await session.execute(
                select(DecisionAcceptance).where(
                    DecisionAcceptance.decision_id == agreement["id"]
                )
            )
        ).scalar_one()
    assert json.loads(row.scope_json) == ["<repository>"]

    res = await client.get(f"/api/repos/{repo['id']}/decisions/lane-counts")
    assert res.status_code == 200, res.text
    counts = res.json()
    assert (counts["active"], counts["candidates"], counts["uncheckable"]) == (1, 1, 0)


@pytest.mark.asyncio
async def test_blank_files_fall_through_to_modules(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    rec = await _create(
        client,
        repo["id"],
        title="Blank file, real module",
        rationale="why",
        affected_files=["   "],
        affected_modules=["pkg/core"],
    )
    assert rec["status"] == "active"
    assert rec["currency"] == "active"


@pytest.mark.asyncio
async def test_blockers_and_review_signals_over_extracted_records(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    async with get_session(app.state.session_factory) as session:
        mined = await crud.upsert_decision(
            session,
            repository_id=repo["id"],
            title="Mined with evidence",
            context="",
            decision="d",
            rationale="",
            source="inline_marker",
            affected_files=["src/a.py"],
            evidence_commits=["abc123"],
        )
        bare = await crud.upsert_decision(
            session,
            repository_id=repo["id"],
            title="Mined with nothing",
            decision="d2",
            source="inline_marker",
        )
        agreement = await crud.upsert_decision(
            session,
            repository_id=repo["id"],
            title="Mined agreement",
            kind="agreement",
            rationale="r",
            source="inline_marker",
            evidence_file="AGENTS.md",
        )
        assert crud.record_blockers(mined) == [
            "no rationale or explicit constraint reason"
        ]
        assert candidate_review_signals(mined) == (0.0, False)
        assert crud.record_blockers(bare) == [
            "no rationale or explicit constraint reason",
            "no scope: name the files or modules it governs",
            "no evidence reference",
        ]
        assert candidate_review_signals(bare) == (0.0, True)
        assert crud.record_blockers(agreement) == []
        assert candidate_review_signals(agreement) == (1.0, False)
        assert crud.names_a_scope(agreement) == []
