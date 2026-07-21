"""Tests for git intelligence endpoints."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from tests.unit.server.conftest import create_test_repo

_LAST_FIX_AT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


async def _insert_git_metadata(session_factory, repo_id: str) -> None:
    """Insert test git metadata."""
    async with get_session(session_factory) as session:
        await crud.upsert_git_metadata(
            session,
            repository_id=repo_id,
            file_path="src/main.py",
            commit_count_total=50,
            commit_count_90d=10,
            commit_count_30d=3,
            primary_owner_name="Alice",
            primary_owner_email="alice@example.com",
            primary_owner_commit_pct=0.6,
            top_authors_json=json.dumps([{"name": "Alice", "commits": 30}]),
            significant_commits_json=json.dumps([{"sha": "abc", "message": "init"}]),
            co_change_partners_json=json.dumps(
                [{"file_path": "src/utils.py", "co_change_count": 5}]
            ),
            is_hotspot=True,
            is_stable=False,
            churn_percentile=0.85,
            age_days=365,
            change_entropy=1.5,
            change_entropy_pct=0.9,
            prior_defect_count=4,
            bug_magnet=True,
            last_fix_at=_LAST_FIX_AT,
            temporal_hotspot_score=12.3,
            commit_count_capped=True,
            original_path="src/old_main.py",
            fix_symbol_counts_json=json.dumps({"src/main.py::run": 3}),
        )
        await crud.upsert_git_metadata(
            session,
            repository_id=repo_id,
            file_path="src/utils.py",
            commit_count_total=20,
            commit_count_90d=0,
            commit_count_30d=0,
            primary_owner_name="Bob",
            primary_owner_email="bob@example.com",
            primary_owner_commit_pct=0.9,
            is_hotspot=False,
            is_stable=True,
            churn_percentile=0.2,
            age_days=200,
        )


@pytest.mark.asyncio
async def test_get_git_metadata(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_git_metadata(app.state.session_factory, repo["id"])

    resp = await client.get(
        f"/api/repos/{repo['id']}/git-metadata",
        params={"file_path": "src/main.py"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["file_path"] == "src/main.py"
    assert data["commit_count_total"] == 50
    assert data["is_hotspot"] is True
    assert data["primary_owner_name"] == "Alice"
    # Newly surfaced change-complexity + defect-history signals.
    assert data["change_entropy"] == 1.5
    assert data["change_entropy_pct"] == 90.0  # normalized 0-1 -> 0-100
    assert data["prior_defect_count"] == 4
    assert data["temporal_hotspot_score"] == 12.3
    assert data["commit_count_capped"] is True
    assert data["original_path"] == "src/old_main.py"


@pytest.mark.asyncio
async def test_file_detail_carries_symbol_fix_counts(client: AsyncClient, app) -> None:
    """The file page answers "which symbol keeps breaking" without a second call.

    The map is joined onto the file-detail git block rather than onto
    HotspotResponse, so the hotspots list is not made to carry a per-symbol
    dict on every row.
    """
    repo = await create_test_repo(client)
    await _insert_git_metadata(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/files/src/main.py")
    assert resp.status_code == 200, resp.text
    git = resp.json()["git"]
    assert git["fix_symbol_counts"] == {"src/main.py::run": 3}

    # A file the rollup never touched reports an empty map, not a missing key,
    # so a consumer can index into it unconditionally.
    resp = await client.get(f"/api/repos/{repo['id']}/files/src/utils.py")
    assert resp.status_code == 200
    assert resp.json()["git"]["fix_symbol_counts"] == {}


@pytest.mark.asyncio
async def test_get_git_metadata_not_found(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(
        f"/api/repos/{repo['id']}/git-metadata",
        params={"file_path": "nonexistent.py"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_hotspots(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_git_metadata(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/hotspots")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1  # Only main.py is a hotspot
    assert payload["has_more"] is False
    data = payload["items"]
    assert len(data) == 1
    assert data[0]["file_path"] == "src/main.py"
    assert data[0]["is_hotspot"] is True
    assert data[0]["change_entropy_pct"] == 90.0
    assert data[0]["prior_defect_count"] == 4
    assert data[0]["original_path"] == "src/old_main.py"
    # The magnet flag rides with its timestamp: consumers must be able to put
    # an age beside it, or they drop the flag rather than show it unanchored.
    assert data[0]["bug_magnet"] is True
    assert data[0]["last_fix_at"].startswith("2026-03-01T12:00:00")


@pytest.mark.asyncio
async def test_hotspots_omit_fix_flag_when_never_fixed(client: AsyncClient, app) -> None:
    """A file with no counted fixes reports the flag off and no timestamp."""
    repo = await create_test_repo(client)
    async with get_session(app.state.session_factory) as session:
        await crud.upsert_git_metadata(
            session,
            repository_id=repo["id"],
            file_path="src/clean.py",
            commit_count_total=30,
            commit_count_90d=9,
            commit_count_30d=2,
            is_hotspot=True,
            is_stable=False,
            churn_percentile=0.8,
            age_days=100,
        )

    resp = await client.get(f"/api/repos/{repo['id']}/hotspots")
    assert resp.status_code == 200
    row = resp.json()["items"][0]
    assert row["prior_defect_count"] == 0
    assert row["bug_magnet"] is False
    assert row["last_fix_at"] is None


@pytest.mark.asyncio
async def test_get_ownership(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_git_metadata(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/ownership")
    assert resp.status_code == 200
    payload = resp.json()
    data = payload["items"]
    assert payload["total"] == len(data)
    assert len(data) >= 1
    # Both files are under "src" module
    src_entry = next((e for e in data if e["module_path"] == "src"), None)
    assert src_entry is not None
    assert src_entry["file_count"] == 2


@pytest.mark.asyncio
async def test_get_co_changes(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_git_metadata(app.state.session_factory, repo["id"])

    resp = await client.get(
        f"/api/repos/{repo['id']}/co-changes",
        params={"file_path": "src/main.py", "min_count": 3},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["file_path"] == "src/main.py"
    assert len(data["co_change_partners"]) == 1
    assert data["co_change_partners"][0]["file_path"] == "src/utils.py"


async def _insert_git_commits(session_factory, repo_id: str) -> None:
    """Insert a small spread of commits with distinct risk scores."""
    from datetime import UTC, datetime

    def _row(sha: str, risk: float, ts: int, **over) -> dict:
        base = {
            "sha": sha,
            "author_name": "Ann",
            "author_email": "ann@example.com",
            "committed_at": datetime.fromtimestamp(ts, tz=UTC),
            "subject": f"commit {sha}",
            "lines_added": 40,
            "lines_deleted": 5,
            "files_changed": 4,
            "dirs_changed": 2,
            "subsystems_changed": 1,
            "entropy": 1.2,
            "is_fix": False,
            "author_experience": 3,
            "change_risk_score": risk,
            "change_risk_level": "high" if risk >= 7 else "moderate" if risk >= 4 else "low",
        }
        base.update(over)
        return base

    async with get_session(session_factory) as session:
        await crud.upsert_git_commits_bulk(
            session,
            repo_id,
            [
                _row("aaaaaaaa11", 2.0, 3000),
                _row("bbbbbbbb22", 8.5, 1000),
                _row("cccccccc33", 5.0, 2000),
            ],
        )


@pytest.mark.asyncio
async def test_get_commits_sorted_by_risk(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_git_commits(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/commits", params={"sort": "risk"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 3
    items = payload["items"]
    # Risk-descending review-priority order.
    assert [c["short_sha"] for c in items] == ["bbbbbbbb", "cccccccc", "aaaaaaaa"]
    top = items[0]
    assert top["change_risk_score"] == 8.5
    # Repo-relative normalization: the top commit is the highest percentile and
    # falls in the top tercile (portable, not the absolute calibration band).
    assert top["risk_percentile"] > items[-1]["risk_percentile"]
    assert top["review_priority"] == "high"
    assert items[-1]["review_priority"] == "low"


@pytest.mark.asyncio
async def test_get_commits_sorted_by_date(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_git_commits(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/commits", params={"sort": "date"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [c["short_sha"] for c in items] == ["aaaaaaaa", "cccccccc", "bbbbbbbb"]


@pytest.mark.asyncio
async def test_get_commit_detail_has_drivers(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_git_commits(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/commits/bbbbbbbb")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sha"] == "bbbbbbbb22"
    assert data["author_experience"] == 3
    # Re-scoring the stored features reproduces the persisted score exactly.
    assert data["change_risk_score"] == 8.5
    assert len(data["drivers"]) == 7  # la, ld, nf, nd, ns, entropy, exp
    feats = {d["feature"] for d in data["drivers"]}
    assert {"la", "exp", "entropy"} <= feats


@pytest.mark.asyncio
async def test_get_commit_not_found(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(f"/api/repos/{repo['id']}/commits/deadbeef")
    assert resp.status_code == 404


async def _insert_agent_commit(session_factory, repo_id: str) -> None:
    from datetime import UTC, datetime

    async with get_session(session_factory) as session:
        await crud.upsert_git_commits_bulk(
            session,
            repo_id,
            [
                {
                    "sha": "dddddddd44",
                    "author_name": "claude",
                    "author_email": "bot@example.com",
                    "committed_at": datetime.fromtimestamp(4000, tz=UTC),
                    "subject": "agent commit",
                    "lines_added": 10,
                    "lines_deleted": 2,
                    "files_changed": 1,
                    "dirs_changed": 1,
                    "subsystems_changed": 1,
                    "entropy": 0.5,
                    "is_fix": False,
                    "author_experience": 1,
                    "change_risk_score": 3.0,
                    "change_risk_level": "low",
                    "agent_name": "claude-code",
                    "agent_autonomy_tier": 2,
                    "agent_channel": "git_footer",
                    "agent_confidence": "high",
                }
            ],
        )


@pytest.mark.asyncio
async def test_commits_carry_agent_provenance_and_top_driver(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_git_commits(app.state.session_factory, repo["id"])
    await _insert_agent_commit(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/commits", params={"sort": "date"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items[0]["sha"] == "dddddddd44"
    assert items[0]["agent_name"] == "claude-code"
    assert items[0]["agent_autonomy_tier"] == 2
    assert items[0]["agent_confidence"] == "high"
    assert items[0]["author_experience"] == 1
    # Human rows carry no agent attribution but do carry a top driver.
    human = items[-1]
    assert human["agent_name"] is None
    assert isinstance(human["top_driver"], str) and human["top_driver"]

    detail = await client.get(f"/api/repos/{repo['id']}/commits/dddddddd")
    assert detail.status_code == 200
    assert detail.json()["agent_channel"] == "git_footer"


@pytest.mark.asyncio
async def test_commits_carry_the_author_total_behind_the_new_contributor_badge(
    client: AsyncClient, app
) -> None:
    """The badge keys on this, not on ``author_experience``.

    Experience is a running count, so it is near zero for everyone at the old
    edge of the indexed window; a total does not move with a commit's position
    in it. Ann's three commits must all report 3, including her earliest.
    """
    repo = await create_test_repo(client)
    await _insert_git_commits(app.state.session_factory, repo["id"])
    await _insert_agent_commit(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/commits", params={"sort": "date"})
    assert resp.status_code == 200
    items = {c["short_sha"]: c for c in resp.json()["items"]}

    assert items["aaaaaaaa"]["author_commit_count"] == 3
    assert items["bbbbbbbb"]["author_commit_count"] == 3
    assert items["cccccccc"]["author_commit_count"] == 3
    # A different author with a single commit is the genuine new contributor.
    assert items["dddddddd"]["author_commit_count"] == 1

    detail = await client.get(f"/api/repos/{repo['id']}/commits/aaaaaaaa")
    assert detail.status_code == 200
    assert detail.json()["author_commit_count"] == 3


@pytest.mark.asyncio
async def test_commits_authorship_filter(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_git_commits(app.state.session_factory, repo["id"])
    await _insert_agent_commit(app.state.session_factory, repo["id"])

    agents = await client.get(f"/api/repos/{repo['id']}/commits", params={"authorship": "agent"})
    assert agents.status_code == 200
    assert agents.json()["total"] == 1
    assert agents.json()["items"][0]["agent_name"] == "claude-code"

    humans = await client.get(f"/api/repos/{repo['id']}/commits", params={"authorship": "human"})
    assert humans.json()["total"] == 3
    assert all(c["agent_name"] is None for c in humans.json()["items"])


@pytest.mark.asyncio
async def test_commit_stats_risk_histogram(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_git_commits(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/commits/stats")
    assert resp.status_code == 200
    data = resp.json()

    hist = data["risk_histogram"]
    assert len(hist) == 20  # 0.5-wide bins across the 0-10 score axis
    assert sum(b["count"] for b in hist) == 3
    # Fixture scores 2.0 / 5.0 / 8.5 land in their own bins, nowhere else.
    filled = {b["start"]: b["count"] for b in hist if b["count"]}
    assert filled == {2.0: 1, 5.0: 1, 8.5: 1}

    # The cuts must sit inside the distribution and keep their order, so the
    # chart's dashed lines land where the priority pills change.
    assert data["moderate_cut"] < data["high_cut"]
    assert 2.0 <= data["moderate_cut"] <= 8.5


@pytest.mark.asyncio
async def test_commit_stats_histogram_empty_without_scores(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)

    resp = await client.get(f"/api/repos/{repo['id']}/commits/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["risk_histogram"] == []
    assert data["moderate_cut"] is None
    assert data["high_cut"] is None


@pytest.mark.asyncio
async def test_agent_trend(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_git_commits(app.state.session_factory, repo["id"])
    await _insert_agent_commit(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/commits/agent-trend")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_commits"] == 4
    assert data["agent_commits"] == 1
    assert data["agent_pct"] == 25.0
    assert data["agent_names"] == [{"name": "claude-code", "count": 1}]
    # All four fixture commits share the epoch-1970 month bucket.
    assert len(data["buckets"]) == 1
    bucket = data["buckets"][0]
    assert bucket["total_commits"] == 4
    assert bucket["tier_counts"] == {"2": 1}


@pytest.mark.asyncio
async def test_get_git_summary(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_git_metadata(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/git-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_files"] == 2
    assert data["hotspot_count"] == 1
    assert data["stable_count"] == 1
    assert len(data["top_owners"]) == 2
