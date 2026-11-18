"""``GET /decisions/agent-prompt`` serves the text ``repowise decision discover`` prints."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from repowise.core.agent_prompts.decisions import render_add
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import DecisionRecord, GitMetadata
from tests.unit.server.conftest import create_test_repo


@pytest.mark.asyncio
async def test_discover_names_the_background_records_on_busy_files(client: AsyncClient, app):
    repo = await create_test_repo(client)
    async with get_session(app.state.session_factory) as session:
        session.add(
            GitMetadata(
                repository_id=repo["id"], file_path="src/app.py", is_hotspot=True,
                temporal_hotspot_score=0.8,
            )
        )
        session.add(
            DecisionRecord(
                id="bg1", repository_id=repo["id"], title="Keep the router pure",
                decision="no I/O in routing", rationale="tests", status="proposed",
                source="pr", affected_files_json=json.dumps(["src/app.py"]),
            )
        )

    res = await client.get(f"/api/repos/{repo['id']}/decisions/agent-prompt")

    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["kind"], body["ids"], body["count"]) == ("discover", ["bg1"], 1)
    assert "`bg1` Keep the router pure (`src/app.py`)" in body["text"]


@pytest.mark.asyncio
async def test_add_and_empty_agreements(client: AsyncClient):
    repo = await create_test_repo(client)

    add = (await client.get(f"/api/repos/{repo['id']}/decisions/agent-prompt")).json()
    agreements = (
        await client.get(
            f"/api/repos/{repo['id']}/decisions/agent-prompt", params={"kind": "agreements"}
        )
    ).json()

    assert add == {"kind": "add", "text": render_add(), "ids": [], "count": 0}
    assert agreements == {"kind": "agreements", "text": "", "ids": [], "count": 0}


@pytest.mark.asyncio
async def test_unknown_kind_is_refused(client: AsyncClient):
    repo = await create_test_repo(client)
    res = await client.get(
        f"/api/repos/{repo['id']}/decisions/agent-prompt", params={"kind": "everything"}
    )
    assert res.status_code == 422
