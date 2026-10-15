"""GET /api/repos/{id}/events: stored events as CloudEvents, filtered by time and kind."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import EngineEventRecord
from tests.unit.server.conftest import create_test_repo


async def _seed(app, repo_id: str) -> None:
    async with get_session(app.state.session_factory) as session:
        for i, kind in enumerate(("new_hotspot", "dead_code_growth"), start=1):
            session.add(
                EngineEventRecord(
                    id=f"{i:032d}",
                    repository_id=repo_id,
                    kind=kind,
                    component="core",
                    severity="high",
                    occurred_at=datetime(2026, 10, i, tzinfo=UTC),
                    commit_sha="b" * 40,
                    base_commit_sha="a" * 40,
                    stamp="h40.p8",
                    payload_json=json.dumps(
                        {"evidence": {"n": i}, "vcs": {"vcs.change.id": "pr-7"}, "source": ""}
                    ),
                )
            )


@pytest.mark.asyncio
async def test_events_are_cloudevents_oldest_first(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    url = f"/api/repos/{repo['id']}/events"

    events = (await client.get(url)).json()["events"]
    assert [e["type"] for e in events] == [
        "dev.repowise.hotspot.added.0.1.0",
        "dev.repowise.deadcode.grew.0.1.0",
    ]
    first = events[0]
    assert first["specversion"] == "1.0" and first["subject"] == "component:core"
    assert first["source"] == f"/repos/{repo['id']}"
    assert first["data"]["vcs.change.id"] == "pr-7"
    assert first["data"]["vcs.ref.head.revision"] == "b" * 40
    assert first["data"]["evidence"] == {"n": 1}

    later = (await client.get(url, params={"since": "2026-10-02T00:00:00"})).json()["events"]
    assert [e["id"] for e in later] == [f"{2:032d}"]
    only = (await client.get(url, params={"kind": "new_hotspot"})).json()["events"]
    assert [e["id"] for e in only] == [f"{1:032d}"]
    assert (await client.get(url, params={"kind": "nope"})).status_code == 422
    assert (await client.get("/api/repos/nope/events")).status_code == 404


@pytest.mark.asyncio
async def test_an_empty_list_says_why(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    url = f"/api/repos/{repo['id']}/events"

    empty = (await client.get(url)).json()
    assert empty["events"] == [] and empty["snapshot_runs"] == 0
    assert empty["empty_reason"] == "baseline_only"
    assert "start after the next `repowise update` on a new commit" in empty["empty_note"]

    await _seed(app, repo["id"])
    full = (await client.get(url)).json()
    assert full["empty_reason"] is None and full["empty_note"] is None
