"""The SQL percentile recompute ranks code rows only; history-tier rows read 0."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from repowise.core.persistence.crud import recompute_git_percentiles, upsert_git_metadata_bulk
from repowise.core.persistence.models import GitMetadata
from tests.unit.persistence.helpers import insert_repo


async def test_sql_recompute_ranks_code_rows_only(async_session) -> None:
    repo = await insert_repo(async_session)
    code = [
        {"file_path": f"m{i}.py", "temporal_hotspot_score": float(i), "commit_count_90d": 9}
        for i in range(4)
    ]
    docs = [
        {"file_path": f"d{i}.md", "history_only": True, "commit_count_90d": 50} for i in range(8)
    ]
    await upsert_git_metadata_bulk(async_session, repo.id, code + docs)
    await recompute_git_percentiles(async_session, repo.id)

    rows = (
        (
            await async_session.execute(
                select(GitMetadata).where(GitMetadata.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    )
    pct = {r.file_path: r.churn_percentile for r in rows}
    # Ranked among the four code rows alone: 0, 1/3, 2/3, 1.
    assert [round(pct[f"m{i}.py"], 3) for i in range(4)] == [0.0, 0.333, 0.667, 1.0]
    for r in rows:
        if r.history_only:
            assert r.churn_percentile == 0.0
            assert r.is_hotspot is False
            assert r.prior_defect_pct == 0.0


async def test_tied_entropy_ranks_by_path_in_both_paths(async_session) -> None:
    """A tie ranks by path in SQL and in Python, whatever order the rows arrive in."""
    from repowise.core.ingestion.git_indexer.enrich import compute_percentiles

    rows = [
        {"file_path": path, "change_entropy": 0.5, "co_change_mass": 2.0}
        for path in ("c.py", "a.py", "b.py")
    ]
    repo = await insert_repo(async_session)
    await upsert_git_metadata_bulk(async_session, repo.id, [dict(r) for r in reversed(rows)])
    await recompute_git_percentiles(async_session, repo.id)
    stored = (
        (
            await async_session.execute(
                select(GitMetadata).where(GitMetadata.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    )

    compute_percentiles(rows)

    expected = {"a.py": 0.0, "b.py": 1 / 3, "c.py": 2 / 3}
    assert {r["file_path"]: r["change_entropy_pct"] for r in rows} == expected
    assert {r["file_path"]: r["co_change_scatter_pct"] for r in rows} == expected
    assert {r.file_path: r.change_entropy_pct for r in stored} == pytest.approx(expected)
    assert {r.file_path: r.co_change_scatter_pct for r in stored} == pytest.approx(expected)


async def test_deleted_rows_leave_before_the_percentiles_are_ranked(async_session) -> None:
    from repowise.core.pipeline.persist import persist_git_refresh

    repo = await insert_repo(async_session)
    rows = [
        {"file_path": f"m{i}.py", "temporal_hotspot_score": float(i), "commit_count_90d": 9}
        for i in range(3)
    ]
    await upsert_git_metadata_bulk(async_session, repo.id, rows)

    await persist_git_refresh(
        async_session, repo.id, {r["file_path"]: r for r in rows}, None, None,
        gone_paths={"m2.py"},
    )

    stored = (
        (await async_session.execute(select(GitMetadata).where(GitMetadata.repository_id == repo.id)))
        .scalars()
        .all()
    )
    assert {r.file_path: r.churn_percentile for r in stored} == {"m0.py": 0.0, "m1.py": 1.0}
