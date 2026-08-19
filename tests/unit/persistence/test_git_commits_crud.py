"""CRUD round-trip tests for the git_commits table."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repowise.core.persistence.crud import (
    count_git_commits,
    delete_git_commits,
    delete_git_commits_by_sha,
    get_author_commit_counts,
    get_commit_experience_inputs,
    get_commit_risk_scores,
    get_git_commit,
    get_git_commits,
    get_latest_commit_committed_at,
    upsert_git_commits_bulk,
)
from tests.unit.persistence.helpers import insert_repo


def _row(sha: str, *, risk: float, ts: int, **over) -> dict:
    base = {
        "sha": sha,
        "author_name": "Ann",
        "author_email": "ann@x",
        "committed_at": datetime.fromtimestamp(ts, tz=UTC),
        "subject": f"commit {sha}",
        "lines_added": 10,
        "lines_deleted": 2,
        "files_changed": 3,
        "dirs_changed": 1,
        "subsystems_changed": 1,
        "entropy": 0.5,
        "is_fix": False,
        "change_risk_score": risk,
        "change_risk_level": "high" if risk >= 7 else "moderate" if risk >= 4 else "low",
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_upsert_and_query_by_risk_and_date(async_session) -> None:
    repo = await insert_repo(async_session)
    rows = [
        _row("aaa111", risk=2.0, ts=3000),
        _row("bbb222", risk=8.5, ts=1000),
        _row("ccc333", risk=5.0, ts=2000),
    ]
    await upsert_git_commits_bulk(async_session, repo.id, rows)
    await async_session.commit()

    assert await count_git_commits(async_session, repo.id) == 3

    by_risk = await get_git_commits(async_session, repo.id, sort="risk")
    assert [c.sha for c in by_risk] == ["bbb222", "ccc333", "aaa111"]

    by_date = await get_git_commits(async_session, repo.id, sort="date")
    assert [c.sha for c in by_date] == ["aaa111", "ccc333", "bbb222"]


@pytest.mark.asyncio
async def test_pagination(async_session) -> None:
    repo = await insert_repo(async_session)
    rows = [_row(f"sha{i:03d}", risk=float(i), ts=1000 + i) for i in range(10)]
    await upsert_git_commits_bulk(async_session, repo.id, rows)
    await async_session.commit()

    page1 = await get_git_commits(async_session, repo.id, sort="risk", limit=3, offset=0)
    page2 = await get_git_commits(async_session, repo.id, sort="risk", limit=3, offset=3)
    assert [c.sha for c in page1] == ["sha009", "sha008", "sha007"]
    assert [c.sha for c in page2] == ["sha006", "sha005", "sha004"]


@pytest.mark.asyncio
async def test_get_by_sha_prefix(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_git_commits_bulk(async_session, repo.id, [_row("abcdef1234", risk=3.0, ts=1000)])
    await async_session.commit()

    got = await get_git_commit(async_session, repo.id, "abcdef")
    assert got is not None
    assert got.sha == "abcdef1234"
    assert got.change_risk_level == "low"
    assert await get_git_commit(async_session, repo.id, "nope") is None


@pytest.mark.asyncio
async def test_upsert_is_idempotent_on_sha(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_git_commits_bulk(async_session, repo.id, [_row("dup", risk=3.0, ts=1000)])
    await async_session.commit()
    # Re-upsert same sha with a new risk → update, not duplicate.
    await upsert_git_commits_bulk(
        async_session, repo.id, [_row("dup", risk=9.0, ts=1000, change_risk_level="high")]
    )
    await async_session.commit()

    assert await count_git_commits(async_session, repo.id) == 1
    got = await get_git_commit(async_session, repo.id, "dup")
    assert got.change_risk_score == 9.0
    assert got.change_risk_level == "high"


@pytest.mark.asyncio
async def test_get_commit_risk_scores(async_session) -> None:
    repo = await insert_repo(async_session)
    rows = [
        _row("aaa", risk=2.0, ts=3000),
        _row("bbb", risk=8.5, ts=1000),
        _row("ccc", risk=5.0, ts=2000, change_risk_score=None, change_risk_level=None),
    ]
    await upsert_git_commits_bulk(async_session, repo.id, rows)
    await async_session.commit()

    scores = await get_commit_risk_scores(async_session, repo.id)
    # Null-scored commits are excluded; order is unspecified, so compare as a set.
    assert sorted(scores) == [2.0, 8.5]


@pytest.mark.asyncio
async def test_get_latest_commit_committed_at(async_session) -> None:
    repo = await insert_repo(async_session)
    assert await get_latest_commit_committed_at(async_session, repo.id) is None
    base = 1_700_000_000
    await upsert_git_commits_bulk(
        async_session,
        repo.id,
        [
            _row("aaa", risk=2.0, ts=base + 300),
            _row("bbb", risk=8.5, ts=base + 100),
            _row("ccc", risk=5.0, ts=base + 200),
        ],
    )
    await async_session.commit()
    latest = await get_latest_commit_committed_at(async_session, repo.id)
    assert latest is not None
    # SQLite may return naive; interpret as UTC for the comparison.
    dt = latest if latest.tzinfo is not None else latest.replace(tzinfo=UTC)
    assert int(dt.timestamp()) == base + 300


@pytest.mark.asyncio
async def test_author_experience_round_trips(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_git_commits_bulk(
        async_session, repo.id, [_row("exp1", risk=3.0, ts=1000, author_experience=42)]
    )
    await async_session.commit()
    got = await get_git_commit(async_session, repo.id, "exp1")
    assert got is not None
    assert got.author_experience == 42


@pytest.mark.asyncio
async def test_delete_git_commits_by_sha(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_git_commits_bulk(
        async_session,
        repo.id,
        [_row(s, risk=1.0, ts=1000) for s in ("keep", "drop1", "drop2")],
    )
    await async_session.commit()

    removed = await delete_git_commits_by_sha(async_session, repo.id, ["drop1", "drop2"])
    await async_session.commit()

    assert removed == 2
    assert await count_git_commits(async_session, repo.id) == 1
    assert await get_git_commit(async_session, repo.id, "keep") is not None


@pytest.mark.asyncio
async def test_get_author_commit_counts_groups_raw_identities(async_session) -> None:
    """Grouped raw, because the identity fold lives in the git-indexer."""
    repo = await insert_repo(async_session)
    await upsert_git_commits_bulk(
        async_session,
        repo.id,
        [
            _row("a1", risk=1.0, ts=1000),
            _row("a2", risk=1.0, ts=2000),
            _row("b1", risk=1.0, ts=3000, author_name="Bob", author_email="bob@x"),
        ],
    )
    await async_session.commit()

    counts = {
        (name, email): n
        for name, email, n in await get_author_commit_counts(async_session, repo.id)
    }

    assert counts[("Ann", "ann@x")] == 2
    assert counts[("Bob", "bob@x")] == 1


@pytest.mark.asyncio
async def test_get_commit_experience_inputs_carries_the_scoring_features(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_git_commits_bulk(
        async_session, repo.id, [_row("a1", risk=6.0, ts=1000, author_experience=7)]
    )
    await async_session.commit()

    rows = await get_commit_experience_inputs(async_session, repo.id)

    assert len(rows) == 1
    row = rows[0]
    # Everything score_change needs, so the reconcile never has to touch git.
    assert row["sha"] == "a1"
    assert row["author_experience"] == 7
    assert row["lines_added"] == 10
    assert row["entropy"] == 0.5
    assert row["change_risk_score"] == 6.0


@pytest.mark.asyncio
async def test_delete_git_commits(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_git_commits_bulk(async_session, repo.id, [_row("x", risk=1.0, ts=1000)])
    await async_session.commit()
    await delete_git_commits(async_session, repo.id)
    await async_session.commit()
    assert await count_git_commits(async_session, repo.id) == 0
