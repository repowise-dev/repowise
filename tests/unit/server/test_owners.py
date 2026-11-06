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
async def test_owner_profile_has_no_ai_figures_and_matches_totals(
    client: AsyncClient, app
) -> None:
    """AI attribution is reported per file, component and repo, never per person."""
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])
    async with get_session(app.state.session_factory) as session:
        await crud.upsert_git_metadata(
            session,
            repository_id=repo["id"],
            file_path="src/main.py",
            agent_commit_count=10,
            attribution_known_count=20,
            agent_authored_pct=0.5,
            agent_tier_counts_json=json.dumps({"2": 7, "3": 3}),
        )

    key = quote("alice@example.com", safe="")
    resp = await client.get(f"/api/repos/{repo['id']}/owners/{key}")
    assert resp.status_code == 200
    data = resp.json()
    assert not any(k.startswith("agent") for k in data)
    # Truncation totals match the (small) fixture exactly.
    assert data["files_touched_total"] == len(data["top_files"])
    assert data["co_authors_total"] == len(data["co_authors"])


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


@pytest.mark.asyncio
async def test_commits_90d_counts_the_owners_own_commits(client: AsyncClient, app) -> None:
    """The directory's 90-day count is distinct commits from the commit table,
    anchored to the newest commit, and unknown (null) without that table."""
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/owners")
    assert {i["commit_count_90d"] for i in resp.json()["items"]} == {None}

    head = datetime(2025, 3, 1, tzinfo=UTC)
    rows = [
        ("a1", "Alice", "alice@example.com", head),
        ("a2", "Alice", "alice@example.com", head.replace(month=2)),
        ("b1", "Bob", "bob@example.com", head.replace(year=2024)),
    ]
    async with get_session(app.state.session_factory) as session:
        await crud.upsert_git_commits_bulk(
            session,
            repo["id"],
            [
                {"sha": sha, "author_name": name, "author_email": email, "committed_at": at}
                for sha, name, email, at in rows
            ],
        )
        await crud.update_repo_git_totals(session, repo["id"], total_commit_count=3)

    resp = await client.get(f"/api/repos/{repo['id']}/owners")
    by_name = {i["name"]: i for i in resp.json()["items"]}
    assert by_name["Alice"]["commit_count_90d"] == 2
    assert by_name["Bob"]["commit_count_90d"] == 0
    assert by_name["Bob"]["last_commit_at"].startswith("2024-03-01")


@pytest.mark.asyncio
async def test_list_and_profile_carry_kind_and_merged_identities(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    items = (await client.get(f"/api/repos/{repo['id']}/owners")).json()["items"]
    assert {i["kind"] for i in items} == {"human"}

    key = quote("alice@example.com", safe="")
    data = (await client.get(f"/api/repos/{repo['id']}/owners/{key}")).json()
    assert data["kind"] == "human"
    assert data["aliases"] == {"names": ["Alice"], "emails": ["alice@example.com"]}


@pytest.mark.asyncio
async def test_list_owners_filters_by_kind(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])
    bot = {"name": "dependabot[bot]", "email": "49699333+dependabot[bot]@users.noreply.github.com"}
    async with get_session(app.state.session_factory) as session:
        await crud.upsert_git_metadata(
            session,
            repository_id=repo["id"],
            file_path="requirements.txt",
            commit_count_total=4,
            primary_owner_name=bot["name"],
            primary_owner_email=bot["email"],
            top_authors_json=json.dumps([{**bot, "commit_count": 4}]),
            bus_factor=1,
            contributor_count=1,
        )

    url = f"/api/repos/{repo['id']}/owners"
    every = (await client.get(url)).json()
    people = (await client.get(url, params={"kind": "human"})).json()
    bots = (await client.get(url, params={"kind": "bot"})).json()
    assert {i["kind"] for i in every["items"]} == {"human", "bot"}
    assert {i["kind"] for i in people["items"]} == {"human"}
    assert people["total"] == every["total"] - bots["total"]
    assert [i["name"] for i in bots["items"]] == ["dependabot[bot]"]
    assert (await client.get(url, params={"kind": "robot"})).status_code == 422


@pytest.mark.asyncio
async def test_owners_summary(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/owners/summary")
    assert resp.status_code == 200
    data = resp.json()
    # Alice and Bob own one file each: it takes both to pass half.
    assert data["truck_factor"] == 2
    assert (data["owned_files"], data["tracked_files"]) == (2, 2)
    assert (data["single_owner_files"], data["single_owner_hotspots"]) == (1, 1)
    assert data["concentration"]["share"] == 1.0
    # Two people split src evenly, so it is no silo.
    assert data["silo_modules"] == []
    assert data["activity_complete"] is False
    assert data["quiet_owners"] == []


@pytest.mark.asyncio
async def test_knowledge_matrix(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/owners/knowledge-matrix")
    assert resp.status_code == 200
    data = resp.json()
    assert data["dirs"] == ["src"]
    keys = [a["key"] for a in data["authors"]]
    assert sorted(keys) == ["alice@example.com", "bob@example.com"]
    a, b = keys.index("alice@example.com"), keys.index("bob@example.com")
    main = next(f for f in data["files"] if f[1] == "main.py")
    # No health row: score null, nloc 0.
    assert main == [0, "main.py", 0, None, True, 1, a, [[a, 0.7], [b, 0.3]], True]
    utils = next(f for f in data["files"] if f[1] == "utils.py")
    assert utils[8] is True  # two commits in the window


@pytest.mark.asyncio
async def test_owner_fold_is_built_once_per_index_state(
    client: AsyncClient, app, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.server.services import owner_profile

    calls = []
    real = owner_profile._load_owner_data

    async def counting(session, repo_id):
        calls.append(repo_id)
        return await real(session, repo_id)

    monkeypatch.setattr(owner_profile, "_load_owner_data", counting)
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    base = f"/api/repos/{repo['id']}/owners"
    for url in (base, f"{base}/summary", f"{base}/knowledge-matrix", base):
        assert (await client.get(url)).status_code == 200
    assert calls == [repo["id"]]
