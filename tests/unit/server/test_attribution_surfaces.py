"""AI attribution on the HTTP surfaces: honest shares, and nothing when it is off.

With attribution on, every share is of commits with a known source and travels
with its coverage; unknown commits are never counted as human. With
``attribution.enabled: false`` in the repo config, no endpoint carries an AI
field, even when an earlier index stored some.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import pytest
from httpx import AsyncClient

from repowise.core import attribution
from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from tests.unit.server.conftest import create_test_repo


@pytest.fixture(autouse=True)
def _private_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user_file = tmp_path / "home" / ".repowise" / "config.yaml"
    monkeypatch.setattr(attribution, "user_config_path", lambda: user_file)


def _commit(sha: str, ts: int, **over) -> dict:
    row = {
        "sha": sha,
        "author_name": "Ann",
        "author_email": "ann@example.com",
        "committed_at": datetime.fromtimestamp(ts, tz=UTC),
        "subject": f"commit {sha}",
        "change_risk_score": 2.0,
        "change_risk_level": "low",
    }
    row.update(over)
    return row


async def _seed(app, repo_id: str) -> None:
    """Ten commits: 3 agent, 2 human-attested, 5 unknown (coverage 50%)."""
    agent = {
        "agent_name": "claude",
        "agent_autonomy_tier": 2,
        "agent_channel": "message_footer",
        "agent_confidence": "high",
        "attribution": "agent",
    }
    rows = [_commit(f"a{i:09d}", 1000 + i, **agent) for i in range(3)]
    rows += [
        _commit(f"h{i:09d}", 2000 + i, attribution="human_attested", agent_channel="git_ai_note")
        for i in range(2)
    ]
    rows += [_commit(f"u{i:09d}", 3000 + i, attribution="unknown") for i in range(5)]
    async with get_session(app.state.session_factory) as session:
        await crud.upsert_git_commits_bulk(session, repo_id, rows)
        await crud.upsert_git_metadata(
            session,
            repository_id=repo_id,
            file_path="src/main.py",
            commit_count_total=10,
            commit_count_90d=10,
            commit_count_30d=0,
            primary_owner_name="Ann",
            primary_owner_email="ann@example.com",
            primary_owner_commit_pct=1.0,
            top_authors_json=json.dumps([{"name": "Ann", "commit_count": 10}]),
            agent_commit_count=3,
            attribution_known_count=5,
            agent_authored_pct=0.6,
            agent_tier_counts_json=json.dumps({"2": 3}),
        )


def _disable(repo: dict) -> None:
    config = Path(repo["local_path"]) / ".repowise" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("attribution:\n  enabled: false\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_agent_trend_reports_coverage_and_share_of_covered(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])

    data = (await client.get(f"/api/repos/{repo['id']}/commits/agent-trend")).json()
    assert data["enabled"] is True
    assert data["coverage_floor_pct"] == 30.0
    assert (data["agent_commits"], data["human_attested_commits"], data["unknown_commits"]) == (
        3,
        2,
        5,
    )
    assert data["coverage_pct"] == 50.0
    assert data["agent_pct_of_covered"] == 60.0  # 3 of 5 known, never 3 of 10
    assert data["suppressed_reason"] is None
    assert sum(b["unknown_commits"] for b in data["buckets"]) == 5


@pytest.mark.asyncio
async def test_commit_filters_use_the_three_states(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    url = f"/api/repos/{repo['id']}/commits"

    for authorship, expected in (("agent", 3), ("human", 2), ("unknown", 5), ("all", 10)):
        page = (await client.get(url, params={"authorship": authorship, "limit": 50})).json()
        assert page["total"] == expected, authorship
    human = (await client.get(url, params={"authorship": "human"})).json()["items"]
    assert {c["attribution"] for c in human} == {"human_attested"}


@pytest.mark.asyncio
async def test_stats_and_file_shares_are_of_covered(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    rid = repo["id"]

    stats = (await client.get(f"/api/repos/{rid}/commits/stats")).json()
    assert stats["agent_commit_count"] == 3
    assert stats["agent_pct_of_covered"] == 60.0
    assert stats["attribution_coverage_pct"] == 50.0

    meta = (
        await client.get(f"/api/repos/{rid}/git-metadata", params={"file_path": "src/main.py"})
    ).json()
    assert meta["agent_authored_pct"] == pytest.approx(0.6)
    assert meta["attribution_coverage"] == pytest.approx(0.5)
    assert meta["agent_share_suppressed"] is None

    detail = (await client.get(f"/api/repos/{rid}/files/src/main.py")).json()
    assert detail["git"]["agent"]["agent_authored_pct"] == pytest.approx(0.6)

    # Never per person: the owner profile carries no AI figure.
    owner = (await client.get(f"/api/repos/{rid}/owners/{quote('ann@example.com', safe='')}")).json()
    assert "agent_collab" not in owner


@pytest.mark.asyncio
async def test_disabled_attribution_shows_nothing_about_ai(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app, repo["id"])
    _disable(repo)
    rid = repo["id"]

    trend = (await client.get(f"/api/repos/{rid}/commits/agent-trend")).json()
    assert trend["enabled"] is False
    assert trend["buckets"] == [] and trend["agent_names"] == []
    assert trend["agent_commits"] == 0 and trend["agent_pct_of_covered"] is None

    # The filter is ignored rather than revealing which commits were labelled.
    page = (await client.get(f"/api/repos/{rid}/commits", params={"authorship": "agent"})).json()
    assert page["total"] == 10
    for c in page["items"]:
        assert c["agent_name"] is None and c["attribution"] is None

    detail = (await client.get(f"/api/repos/{rid}/commits/a000000000")).json()
    assert detail["agent_name"] is None and detail["agent_channel"] is None

    stats = (await client.get(f"/api/repos/{rid}/commits/stats")).json()
    assert stats["agent_commit_count"] is None
    assert stats["agent_pct_of_covered"] is None

    meta = (
        await client.get(f"/api/repos/{rid}/git-metadata", params={"file_path": "src/main.py"})
    ).json()
    for key in ("agent_commit_count", "agent_authored_pct", "agent_tier_counts"):
        assert meta[key] is None

    file_detail = (await client.get(f"/api/repos/{rid}/files/src/main.py")).json()
    assert file_detail["git"]["agent"] is None
