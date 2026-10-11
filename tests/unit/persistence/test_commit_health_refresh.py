"""The scan must fail quietly without taking the caller's transaction with it."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from repowise.core.analysis.change_health.commit_scan import CommitHealthScan
from repowise.core.persistence.crud import get_commit_health, upsert_git_commits_bulk
from repowise.core.persistence.models import GitCommit
from repowise.core.pipeline.commit_health import recent_shas, refresh_commit_health
from tests.unit.persistence.helpers import insert_repo


def _scan_returning_unwritable_rows(*args, **kwargs):
    """A scan whose rows fail on flush, not before it.

    The failure has to land mid-write: an exception raised before any statement
    leaves the session clean, so it would not exercise the savepoint at all.
    ``change_kind`` is NOT NULL with no default, so the finding insert fails.
    """
    return CommitHealthScan(
        delta_rows=[{"sha": "aaa", "status": "available"}],
        finding_rows=[{"sha": "aaa", "change_finding_id": "f0", "dimension": "defect"}],
        scanned=1,
    )


@pytest.mark.asyncio
async def test_a_failed_write_leaves_the_caller_able_to_keep_writing(
    async_session, monkeypatch
) -> None:
    """The regression: a poisoned session would fail the whole index later."""
    import repowise.core.analysis.change_health.commit_scan as scan_mod

    repo = await insert_repo(async_session)
    monkeypatch.setattr(scan_mod, "scan_commits", _scan_returning_unwritable_rows)

    stats = await refresh_commit_health(async_session, repo.id, "/repo", ["aaa"], limit=5)

    assert stats["stored"] == 0
    assert await get_commit_health(async_session, repo.id, "aaa") is None
    # The caller carries on in the same session, which is the real assertion.
    await upsert_git_commits_bulk(async_session, repo.id, [{"sha": "aaa", "subject": "x"}])
    await async_session.commit()
    rows = (await async_session.execute(select(GitCommit.sha))).scalars().all()
    assert list(rows) == ["aaa"]


@pytest.mark.asyncio
async def test_nothing_to_scan_is_not_an_error(async_session) -> None:
    repo = await insert_repo(async_session)

    assert (await refresh_commit_health(async_session, repo.id, "/repo", []))["scanned"] == 0
    assert (
        await refresh_commit_health(async_session, repo.id, "/repo", ["aaa"], limit=0)
    )["scanned"] == 0
    assert await get_commit_health(async_session, repo.id, "aaa") is None


def test_shas_are_spent_newest_first() -> None:
    """The budget runs out in this order, so it must not come from the walk."""
    rows = [
        {"sha": "old", "committed_at": 1},
        {"sha": "new", "committed_at": 3},
        {"sha": "mid", "committed_at": 2},
        {"sha": "", "committed_at": 9},
    ]

    assert recent_shas(rows) == ["new", "mid", "old"]
    assert recent_shas(None) == []
