"""CRUD round-trip tests for the per-commit health delta tables."""

from __future__ import annotations

import pytest

from repowise.core.persistence.crud import (
    delete_commit_health,
    delete_commit_health_by_sha,
    get_commit_health,
    get_commit_health_findings,
    get_scanned_commit_shas,
    upsert_commit_health_bulk,
)
from tests.unit.persistence.helpers import insert_repo

FINGERPRINT = {
    "analyzer_version": 21,
    "rules_fingerprint": "abc",
    "performance_model_version": 2,
}


def _delta(sha: str, **over) -> dict:
    return {"sha": sha, "status": "available", **FINGERPRINT, "introduced_count": 1, **over}


def _finding(sha: str, fid: str, position: int = 0, **over) -> dict:
    return {
        "sha": sha,
        "change_finding_id": fid,
        "position": position,
        "change_kind": "introduced",
        "dimension": "defect",
        "biomarker_type": "long_method",
        "severity": "high",
        "file_path": "a.py",
        "attribution_basis": "added_lines",
        "reason": "added 40 lines to a method",
        **over,
    }


@pytest.mark.asyncio
async def test_findings_come_back_in_the_order_the_scan_set(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_commit_health_bulk(
        async_session,
        repo.id,
        [_delta("aaa")],
        [_finding("aaa", "f2", 1), _finding("aaa", "f0", 0), _finding("aaa", "f1", 2)],
    )
    await async_session.commit()

    rows = await get_commit_health_findings(async_session, repo.id, "aaa")

    assert [r.change_finding_id for r in rows] == ["f0", "f2", "f1"]


@pytest.mark.asyncio
async def test_a_rescan_drops_findings_the_new_run_no_longer_reports(async_session) -> None:
    """A plain upsert would leave the surplus behind as phantom findings."""
    repo = await insert_repo(async_session)
    await upsert_commit_health_bulk(
        async_session, repo.id, [_delta("aaa")], [_finding("aaa", "f0"), _finding("aaa", "f1")]
    )
    await async_session.commit()

    await upsert_commit_health_bulk(
        async_session, repo.id, [_delta("aaa", introduced_count=1)], [_finding("aaa", "f0")]
    )
    await async_session.commit()

    rows = await get_commit_health_findings(async_session, repo.id, "aaa")
    assert [r.change_finding_id for r in rows] == ["f0"]


@pytest.mark.asyncio
async def test_a_rescan_can_reinstate_a_finding_it_just_deleted(async_session) -> None:
    """Same id, deleted then written again in one call, with no commit between."""
    repo = await insert_repo(async_session)
    await upsert_commit_health_bulk(
        async_session, repo.id, [_delta("aaa")], [_finding("aaa", "f0", severity="low")]
    )
    await async_session.commit()

    await upsert_commit_health_bulk(
        async_session, repo.id, [_delta("aaa")], [_finding("aaa", "f0", severity="critical")]
    )
    await async_session.commit()

    (row,) = await get_commit_health_findings(async_session, repo.id, "aaa")
    assert row.severity == "critical"


@pytest.mark.asyncio
async def test_a_commit_with_no_findings_still_stores_a_row(async_session) -> None:
    """"Analysed clean" and "never scanned" must not be the same result."""
    repo = await insert_repo(async_session)
    await upsert_commit_health_bulk(
        async_session, repo.id, [_delta("aaa", introduced_count=0)], []
    )
    await async_session.commit()

    delta = await get_commit_health(async_session, repo.id, "aaa")

    assert delta is not None
    assert delta.introduced_count == 0
    assert await get_commit_health(async_session, repo.id, "never-scanned") is None


@pytest.mark.asyncio
async def test_a_row_from_another_analyzer_does_not_count_as_scanned(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_commit_health_bulk(
        async_session,
        repo.id,
        [_delta("current"), _delta("older", analyzer_version=20)],
        [],
    )
    await async_session.commit()

    done = await get_scanned_commit_shas(async_session, repo.id, **FINGERPRINT)

    assert done == {"current"}


@pytest.mark.asyncio
async def test_changed_rules_invalidate_a_row_without_a_migration(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_commit_health_bulk(async_session, repo.id, [_delta("aaa")], [])
    await async_session.commit()

    done = await get_scanned_commit_shas(
        async_session, repo.id, **{**FINGERPRINT, "rules_fingerprint": "xyz"}
    )

    assert done == set()


@pytest.mark.asyncio
async def test_rows_leave_with_their_commit(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_commit_health_bulk(
        async_session,
        repo.id,
        [_delta("aaa"), _delta("bbb")],
        [_finding("aaa", "f0"), _finding("bbb", "f0")],
    )
    await async_session.commit()

    removed = await delete_commit_health_by_sha(async_session, repo.id, ["aaa"])
    await async_session.commit()

    assert removed == 1
    assert await get_commit_health(async_session, repo.id, "aaa") is None
    assert await get_commit_health_findings(async_session, repo.id, "aaa") == []
    assert await get_commit_health(async_session, repo.id, "bbb") is not None
    assert len(await get_commit_health_findings(async_session, repo.id, "bbb")) == 1


@pytest.mark.asyncio
async def test_a_clean_reindex_clears_both_tables(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_commit_health_bulk(
        async_session, repo.id, [_delta("aaa")], [_finding("aaa", "f0")]
    )
    await async_session.commit()

    await delete_commit_health(async_session, repo.id)
    await async_session.commit()

    assert await get_commit_health(async_session, repo.id, "aaa") is None
    assert await get_commit_health_findings(async_session, repo.id, "aaa") == []


@pytest.mark.asyncio
async def test_a_full_reindex_wipes_the_health_rows_with_their_commits(async_session) -> None:
    """A rewritten history must not leave rows pointing at vanished shas."""
    from repowise.core.pipeline.persist import replace_git_history

    repo = await insert_repo(async_session)
    await upsert_commit_health_bulk(
        async_session, repo.id, [_delta("gone")], [_finding("gone", "f0")]
    )
    await async_session.commit()

    await replace_git_history(async_session, repo.id, {}, None)
    await async_session.commit()

    assert await get_commit_health(async_session, repo.id, "gone") is None
    assert await get_commit_health_findings(async_session, repo.id, "gone") == []
