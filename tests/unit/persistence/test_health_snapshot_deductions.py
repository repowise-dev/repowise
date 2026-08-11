"""The snapshot column that keeps a floored file's trend alive.

``per_file_scores_json`` stores the clamped score, and the score clamps at 1.0.
A file 12.9 points deep and one 9.1 points deep both persist as ``1.0``, so
their whole history is a flat line and doing the work produces no visible
movement. ``per_file_deductions_json`` carries the pre-clamp magnitude for
those files — and only those, since everywhere else it is ``10 - score``.

These pin the storage half. The read half (``unclamped_score``) lives in
``tests/unit/health/test_trends.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from repowise.core.analysis.health.trends import file_trend
from repowise.core.persistence.crud import (
    list_health_snapshots,
    save_health_snapshot,
    upsert_repository,
)
from repowise.core.persistence.models import HealthSnapshot


async def _snapshot(session, repo_id: str, *, scores, deductions=None, day: int = 0):
    return await save_health_snapshot(
        session,
        repo_id,
        hotspot_health=5.0,
        average_health=5.0,
        worst_performer_path="a.py",
        worst_performer_score=1.0,
        per_file_scores=scores,
        per_file_deductions=deductions,
        taken_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=day),
    )


async def test_deductions_are_stored_alongside_the_scores(async_session, tmp_path) -> None:
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    await _snapshot(
        async_session,
        repo.id,
        scores={"a.py": 1.0, "b.py": 7.5},
        deductions={"a.py": 12.9},
    )

    row = (await async_session.execute(select(HealthSnapshot))).scalars().one()

    assert json.loads(row.per_file_scores_json) == {"a.py": 1.0, "b.py": 7.5}
    assert json.loads(row.per_file_deductions_json) == {"a.py": 12.9}


async def test_the_score_map_keeps_its_shape(async_session, tmp_path) -> None:
    """A sibling column, not a richer value inside the existing blob.

    ``per_file_scores_json`` is parsed as ``{path: score}`` by the trend
    series, the trend route and the snapshot file-count read. A value that
    became a dict would blank every file's series in the first, 500 the second
    and go unnoticed in the third. This asserts the shape is untouched.
    """
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    await _snapshot(async_session, repo.id, scores={"a.py": 1.0}, deductions={"a.py": 11.0})

    row = (await async_session.execute(select(HealthSnapshot))).scalars().one()

    parsed = json.loads(row.per_file_scores_json)
    assert all(isinstance(v, int | float) for v in parsed.values())


async def test_a_writer_that_passes_no_deductions_stores_an_empty_map(
    async_session, tmp_path
) -> None:
    """The default has to be a parseable empty map, not NULL.

    Every reader does ``json.loads(...)`` on it, and a row written by a caller
    that has not been threaded through — or one that predates the column — has
    to read as "no depth recorded" rather than raise.
    """
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    await _snapshot(async_session, repo.id, scores={"a.py": 1.0})

    row = (await async_session.execute(select(HealthSnapshot))).scalars().one()

    assert row.per_file_deductions_json == "{}"
    assert json.loads(row.per_file_deductions_json) == {}


async def test_stored_depth_reaches_the_trend_reader(async_session, tmp_path) -> None:
    """End to end through the DB: two snapshots at 1.0, real movement between.

    The write and the read live in different modules and are joined only by
    the column name, so this is the assertion that they actually meet.
    """
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    await _snapshot(async_session, repo.id, scores={"a.py": 1.0}, deductions={"a.py": 12.9}, day=0)
    await _snapshot(async_session, repo.id, scores={"a.py": 1.0}, deductions={"a.py": 10.4}, day=1)

    snapshots = await list_health_snapshots(async_session, repo.id)
    trend = file_trend(snapshots, "a.py")

    assert [p.score for p in trend.points] == [1.0, 1.0]
    assert [p.unclamped_score for p in trend.points] == [-2.9, -0.4]
    assert trend.delta == 0.0
    assert trend.unclamped_delta == 2.5
