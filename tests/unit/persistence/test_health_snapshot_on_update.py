"""Every health write appends a snapshot built from the stored rows.

The full index used to snapshot from its in-memory report and the incremental
update never snapshotted, so ``health --trend`` only moved on a full re-index.
Both paths now call one writer that reads the repository's rows back, which is
the only view that describes the whole repository after a partial write.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from sqlalchemy import select

from repowise.core.persistence.crud import upsert_repository
from repowise.core.persistence.models import HealthFileMetric, HealthFinding, HealthSnapshot
from repowise.core.pipeline.incremental import persist_partial_health
from repowise.core.pipeline.persist import snapshot_health_from_store


async def _seed(session) -> str:
    repo = await upsert_repository(session, name="r", local_path="/tmp/r")
    session.add_all(
        [
            HealthFileMetric(repository_id=repo.id, file_path="a.py", score=6.0, nloc=100),
            HealthFileMetric(repository_id=repo.id, file_path="b.py", score=9.0, nloc=50),
            HealthFinding(
                repository_id=repo.id,
                file_path="a.py",
                biomarker_type="long_method",
                severity="medium",
                health_impact=1.5,
                status="open",
            ),
        ]
    )
    await session.flush()
    return repo.id


async def _snapshots(session, repo_id: str) -> list[HealthSnapshot]:
    rows = await session.execute(
        select(HealthSnapshot)
        .where(HealthSnapshot.repository_id == repo_id)
        .order_by(HealthSnapshot.taken_at.asc())
    )
    return list(rows.scalars().all())


async def test_snapshot_describes_every_stored_file(async_session) -> None:
    repo_id = await _seed(async_session)

    await snapshot_health_from_store(async_session, repo_id)

    (snap,) = await _snapshots(async_session, repo_id)
    assert json.loads(snap.per_file_scores_json) == {"a.py": 6.0, "b.py": 9.0}
    assert snap.worst_performer_path == "a.py"
    assert snap.worst_performer_score == 6.0


async def test_no_stored_metrics_writes_no_snapshot(async_session) -> None:
    repo = await upsert_repository(async_session, name="empty", local_path="/tmp/empty")
    await snapshot_health_from_store(async_session, repo.id)
    assert await _snapshots(async_session, repo.id) == []


async def test_partial_health_persist_appends_a_snapshot(async_session) -> None:
    """A changed-files write still snapshots the whole repository."""
    repo_id = await _seed(async_session)
    report = SimpleNamespace(
        metrics=[],
        findings=[],
        refactoring_suggestions=[],
        function_blame_rows=[],
        authoritative_paths={"b.py"},
        performance_authoritative_paths=set(),
        performance_plan_policy=None,
    )

    await persist_partial_health(async_session, repo_id, report)

    (snap,) = await _snapshots(async_session, repo_id)
    # Both files, not only the one this run re-scored.
    assert set(json.loads(snap.per_file_scores_json)) == {"a.py", "b.py"}
