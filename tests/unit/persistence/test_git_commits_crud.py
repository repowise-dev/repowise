"""CRUD round-trip tests for the git_commits table."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repowise.core.persistence.crud import (
    count_git_commits,
    delete_git_commits,
    get_git_commit,
    get_git_commits,
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
async def test_delete_git_commits(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_git_commits_bulk(async_session, repo.id, [_row("x", risk=1.0, ts=1000)])
    await async_session.commit()
    await delete_git_commits(async_session, repo.id)
    await async_session.commit()
    assert await count_git_commits(async_session, repo.id) == 0
