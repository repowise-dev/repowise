"""Tests for /api/repos/{id}/owners — contributor profile endpoints."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import quote

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.server.services.owner_profile import aggregate_owners
from tests.unit.server.conftest import create_test_repo


async def _seed(session_factory, repo_id: str) -> None:
    async with get_session(session_factory) as session:
        await crud.upsert_git_metadata(
            session,
            repository_id=repo_id,
            file_path="src/main.py",
            commit_count_total=50,
            commit_count_90d=20,
            commit_count_30d=5,
            primary_owner_name="Alice",
            primary_owner_email="alice@example.com",
            primary_owner_commit_pct=0.7,
            top_authors_json=json.dumps(
                [
                    {"name": "Alice", "email": "alice@example.com", "commit_count": 35},
                    {"name": "Bob", "email": "bob@example.com", "commit_count": 15},
                ]
            ),
            co_change_partners_json=json.dumps(
                [{"file_path": "src/utils.py", "co_change_count": 8}]
            ),
            is_hotspot=True,
            is_stable=False,
            churn_percentile=0.9,
            bus_factor=1,
            contributor_count=2,
            lines_added_90d=300,
            lines_deleted_90d=100,
        )
        await crud.upsert_git_metadata(
            session,
            repository_id=repo_id,
            file_path="src/utils.py",
            commit_count_total=10,
            commit_count_90d=2,
            primary_owner_name="Bob",
            primary_owner_email="bob@example.com",
            primary_owner_commit_pct=0.9,
            top_authors_json=json.dumps(
                [
                    {"name": "Bob", "email": "bob@example.com", "commit_count": 9},
                    {"name": "Alice", "email": "alice@example.com", "commit_count": 1},
                ]
            ),
            is_hotspot=False,
            churn_percentile=0.2,
            bus_factor=3,
            contributor_count=2,
        )


@pytest.mark.asyncio
async def test_list_owners(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/owners")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 2
    names = {item["name"] for item in payload["items"]}
    assert names == {"Alice", "Bob"}

    alice = next(i for i in payload["items"] if i["name"] == "Alice")
    assert alice["files_owned"] == 1
    assert alice["hotspots_owned"] == 1
    assert alice["bus_factor_risk_files"] == 1


@pytest.mark.asyncio
async def test_get_owner_profile(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    key = quote("alice@example.com", safe="")
    resp = await client.get(f"/api/repos/{repo['id']}/owners/{key}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Alice"
    assert data["files_owned"] == 1
    assert data["hotspots_owned"] == 1
    # Alice touched both files (she's in top_authors of utils too).
    paths = {f["file_path"] for f in data["top_files"]}
    assert "src/main.py" in paths
    assert "src/utils.py" in paths
    # Bob should appear as a co-author.
    coauthors = {c["name"] for c in data["co_authors"]}
    assert "Bob" in coauthors


@pytest.mark.asyncio
async def test_owner_profile_agent_collab_and_totals(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])
    # Give Alice's owned file an agent-provenance rollup.
    async with get_session(app.state.session_factory) as session:
        await crud.upsert_git_metadata(
            session,
            repository_id=repo["id"],
            file_path="src/main.py",
            agent_commit_count=10,
            agent_authored_pct=0.2,
            agent_tier_counts_json=json.dumps({"2": 7, "3": 3}),
        )

    key = quote("alice@example.com", safe="")
    resp = await client.get(f"/api/repos/{repo['id']}/owners/{key}")
    assert resp.status_code == 200
    data = resp.json()
    collab = data["agent_collab"]
    assert collab is not None
    assert collab["files_with_agent_commits"] == 1
    assert collab["agent_commit_count"] == 10
    assert collab["agent_share_pct"] == 20.0  # 10 of 50 commits on main.py
    assert collab["tier_counts"] == {"2": 7, "3": 3}
    # Truncation totals match the (small) fixture exactly.
    assert data["files_touched_total"] == len(data["top_files"])
    assert data["co_authors_total"] == len(data["co_authors"])

    # Bob owns only utils.py, which has no provenance rollup -> null.
    bob = await client.get(
        f"/api/repos/{repo['id']}/owners/{quote('bob@example.com', safe='')}"
    )
    assert bob.json()["agent_collab"] is None


@pytest.mark.asyncio
async def test_last_commit_uses_authors_own_timestamp(client: AsyncClient, app) -> None:
    """A contributor's last_commit_at is their *own* last commit to a file,
    not the file's last commit by a co-owner."""
    repo = await create_test_repo(client)
    alice_last = datetime(2026, 1, 10, tzinfo=UTC)
    file_last = datetime(2026, 1, 30, tzinfo=UTC)  # Bob's later commit

    async with get_session(app.state.session_factory) as session:
        await crud.upsert_git_metadata(
            session,
            repository_id=repo["id"],
            file_path="src/shared.py",
            commit_count_total=20,
            first_commit_at=datetime(2025, 1, 1, tzinfo=UTC),
            last_commit_at=file_last,
            primary_owner_name="Bob",
            primary_owner_email="bob@example.com",
            top_authors_json=json.dumps(
                [
                    {
                        "name": "Bob",
                        "email": "bob@example.com",
                        "commit_count": 15,
                        "last_commit_ts": int(file_last.timestamp()),
                        "first_commit_ts": int(datetime(2025, 6, 1, tzinfo=UTC).timestamp()),
                    },
                    {
                        "name": "Alice",
                        "email": "alice@example.com",
                        "commit_count": 5,
                        "last_commit_ts": int(alice_last.timestamp()),
                        "first_commit_ts": int(datetime(2025, 1, 1, tzinfo=UTC).timestamp()),
                    },
                ]
            ),
            contributor_count=2,
        )

    async with get_session(app.state.session_factory) as session:
        accs, _ = await aggregate_owners(session, repo["id"])

    alice = accs["alice@example.com"]
    bob = accs["bob@example.com"]
    # Alice must NOT inherit Bob's later commit to the shared file.
    assert alice.last_commit_at == alice_last
    assert bob.last_commit_at == file_last


@pytest.mark.asyncio
async def test_noreply_and_real_email_collapse_to_one_contributor(
    client: AsyncClient, app
) -> None:
    """One person who committed with a real email on one file and a GitHub
    noreply email on another (same display name) is a single contributor —
    not two."""
    repo = await create_test_repo(client)
    async with get_session(app.state.session_factory) as session:
        # File A: Jane with her real email.
        await crud.upsert_git_metadata(
            session,
            repository_id=repo["id"],
            file_path="src/a.py",
            commit_count_total=10,
            primary_owner_name="Jane Doe",
            primary_owner_email="jane@company.com",
            top_authors_json=json.dumps(
                [{"name": "Jane Doe", "email": "jane@company.com", "commit_count": 10}]
            ),
            contributor_count=1,
        )
        # File B: same Jane, but a squash-merge stamped a noreply email.
        await crud.upsert_git_metadata(
            session,
            repository_id=repo["id"],
            file_path="src/b.py",
            commit_count_total=4,
            primary_owner_name="Jane Doe",
            primary_owner_email="12345+jane@users.noreply.github.com",
            top_authors_json=json.dumps(
                [
                    {
                        "name": "Jane Doe",
                        "email": "12345+jane@users.noreply.github.com",
                        "commit_count": 4,
                    }
                ]
            ),
            contributor_count=1,
        )

    async with get_session(app.state.session_factory) as session:
        accs, _ = await aggregate_owners(session, repo["id"])

    # Exactly one bucket, keyed on the real email, crediting both files.
    people = [a for a in accs.values() if a.key]
    assert len(people) == 1
    jane = people[0]
    assert jane.key == "jane@company.com"
    assert jane.name == "Jane Doe"
    assert set(jane.files_touched) == {"src/a.py", "src/b.py"}

    # The directory endpoint agrees: one contributor.
    resp = await client.get(f"/api/repos/{repo['id']}/owners")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_get_owner_profile_not_found(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(f"/api/repos/{repo['id']}/owners/ghost@nowhere")
    assert resp.status_code == 404
