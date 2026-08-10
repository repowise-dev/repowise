"""The two reads ``/health/overview`` was paying for twice.

``get_health_summary`` internally loads both the metrics and the findings
tables. The overview route had already loaded both for its own payload, so
every request scanned each table twice. ``metrics=`` closed half of that;
these pin the ``findings=`` half and the snapshot-scalar read that replaced a
full-entity load of every retained per-file score map.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from repowise.core.persistence.crud import (
    get_health_snapshot_file_counts,
    get_health_snapshot_headline,
    get_health_summary,
    save_health_findings,
    save_health_metrics,
    save_health_snapshot,
    upsert_repository,
)
from repowise.core.persistence.models import HealthSnapshot


def _metric(path: str, score: float, nloc: int = 10) -> dict:
    return {
        "file_path": path,
        "score": score,
        "max_ccn": 1,
        "max_nesting": 1,
        "nloc": nloc,
        "has_test_file": False,
        "module": "src",
    }


def _finding(path: str, impact: float, *, name: str = "f", dimension: str = "defect") -> dict:
    return {
        "file_path": path,
        "biomarker_type": "complex_method",
        "severity": "high",
        "function_name": name,
        "line_start": 1,
        "line_end": 2,
        "details": {},
        "health_impact": impact,
        "reason": "reason",
        "dimension": dimension,
    }


async def _seed(async_session, tmp_path, metrics, findings):
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    await save_health_metrics(async_session, repo.id, metrics)
    await save_health_findings(async_session, repo.id, findings)
    return repo


async def test_handing_over_findings_matches_loading_them(async_session, tmp_path) -> None:
    """The kwarg is a pure read-elision: same repo, same summary."""
    repo = await _seed(
        async_session,
        tmp_path,
        [_metric("a.py", 3.0), _metric("b.py", 7.0)],
        [
            _finding("a.py", 2.0),
            _finding("a.py", 1.0, name="g", dimension="performance"),
            _finding("b.py", 0.5, dimension="maintainability"),
        ],
    )

    from repowise.core.persistence.crud import get_health_findings, get_health_metrics

    metrics = await get_health_metrics(async_session, repo.id)
    findings = await get_health_findings(async_session, repo.id)

    assert await get_health_summary(async_session, repo.id) == await get_health_summary(
        async_session, repo.id, metrics=metrics, findings=findings
    )


async def test_findings_kwarg_is_actually_used_not_re_read(async_session, tmp_path) -> None:
    """Hand over a deliberately short list; the counts must follow it.

    If the kwarg were ignored the function would re-query and report 3. This is
    the only way to prove the second scan is gone without instrumenting SQL —
    and it is also why the docstring insists callers pass the *unfiltered* set.
    """
    repo = await _seed(
        async_session,
        tmp_path,
        [_metric("a.py", 3.0)],
        [
            _finding("a.py", 2.0),
            _finding("a.py", 1.0, name="g", dimension="performance"),
            _finding("a.py", 0.5, name="h", dimension="maintainability"),
        ],
    )

    from repowise.core.persistence.crud import get_health_findings

    findings = await get_health_findings(async_session, repo.id)
    assert len(findings) == 3

    summary = await get_health_summary(async_session, repo.id, findings=findings[:1])
    assert summary["open_findings"] == 1
    assert summary["performance_findings"] == 0


async def test_snapshot_headline_reads_the_latest_and_counts_the_rest(
    async_session, tmp_path
) -> None:
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    base = datetime(2026, 1, 1, tzinfo=UTC)
    # Inserted newest-first so a headline that trusted insertion order fails.
    for offset, hotspot in ((2, 4.0), (0, 6.0), (1, 5.0)):
        await save_health_snapshot(
            async_session,
            repo.id,
            hotspot_health=hotspot,
            average_health=7.0,
            worst_performer_path="a.py",
            worst_performer_score=1.0,
            per_file_scores={"a.py": 1.0},
            taken_at=base + timedelta(days=offset),
        )

    headline = await get_health_snapshot_headline(async_session, repo.id)

    assert headline.hotspot_health == 4.0
    assert headline.taken_at.replace(tzinfo=UTC) == base + timedelta(days=2)
    assert headline.snapshot_count == 3


async def test_snapshot_headline_on_a_repo_with_no_history(async_session, tmp_path) -> None:
    """No snapshots is not an error: the header renders "not measured"."""
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))

    headline = await get_health_snapshot_headline(async_session, repo.id, recent=12)

    assert headline.hotspot_health is None
    assert headline.taken_at is None
    assert headline.snapshot_count == 0
    assert headline.recent == ()


async def _history(session, repo_id: str, n: int, *, files: int = 1) -> datetime:
    """*n* snapshots one day apart, inserted newest-first."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for offset in reversed(range(n)):
        await save_health_snapshot(
            session,
            repo_id,
            hotspot_health=float(offset),
            average_health=float(offset) / 2,
            worst_performer_path="a.py",
            worst_performer_score=1.0,
            per_file_scores={f"f{i}.py": 1.0 for i in range(files + offset)},
            taken_at=base + timedelta(days=offset),
        )
    return base


async def test_snapshot_headline_window_is_bounded_but_the_count_is_not(
    async_session, tmp_path
) -> None:
    """The read is capped at ``recent``; ``snapshot_count`` still tells the truth.

    This is the whole point of the split. Capping the *read* at the window the
    sparkline plots would have made the count read 12 on a 15-snapshot repo,
    which is the misreport that made the obvious ``limit=2`` fix wrong.
    """
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    base = await _history(async_session, repo.id, 15)

    headline = await get_health_snapshot_headline(async_session, repo.id, recent=12)

    assert headline.snapshot_count == 15
    assert len(headline.recent) == 12
    # Oldest-first, ending on the newest row — the order a trend line plots.
    assert headline.recent[-1].taken_at.replace(tzinfo=UTC) == base + timedelta(days=14)
    assert headline.recent[0].taken_at.replace(tzinfo=UTC) == base + timedelta(days=3)
    assert headline.recent[-1].hotspot_health == 14.0
    assert headline.recent[-1].average_health == 7.0
    # And the scalars agree with what the unwindowed headline reports.
    assert headline.hotspot_health == 14.0


async def test_snapshot_headline_window_never_exceeds_the_history(
    async_session, tmp_path
) -> None:
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    await _history(async_session, repo.id, 3)

    headline = await get_health_snapshot_headline(async_session, repo.id, recent=12)

    assert headline.snapshot_count == 3
    assert len(headline.recent) == 3


async def test_snapshot_headline_asks_for_no_window_by_default(
    async_session, tmp_path
) -> None:
    """The header-only caller pays for no rows it will not read."""
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    await _history(async_session, repo.id, 4)

    headline = await get_health_snapshot_headline(async_session, repo.id)

    assert headline.recent == ()
    assert headline.snapshot_count == 4


async def test_snapshot_file_counts_reads_the_newest_rows_oldest_first(
    async_session, tmp_path
) -> None:
    """``{path: score}`` key counts for the delta, without the other 13 maps."""
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    # Row at day *offset* carries ``files + offset`` entries, so 15 snapshots
    # end on 15 files and the one before it on 14.
    await _history(async_session, repo.id, 15)

    assert await get_health_snapshot_file_counts(async_session, repo.id, limit=2) == [14, 15]
    assert await get_health_snapshot_file_counts(async_session, repo.id, limit=3) == [
        13,
        14,
        15,
    ]


async def test_snapshot_file_counts_matches_the_headline_ordering(
    async_session, tmp_path
) -> None:
    """Both reads must agree on which rows are "the newest two".

    They order in opposite directions — the headline ascends and takes the
    tail, this one descends and takes a ``LIMIT`` — so a tie broken differently
    would pair a delta with the wrong pair of scalars.
    """
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    for files in (1, 2, 3, 4):
        await save_health_snapshot(
            async_session,
            repo.id,
            hotspot_health=float(files),
            average_health=float(files),
            worst_performer_path="a.py",
            worst_performer_score=1.0,
            per_file_scores={f"f{i}.py": 1.0 for i in range(files)},
            taken_at=stamp,  # every row on the same timestamp: id breaks the tie
        )

    headline = await get_health_snapshot_headline(async_session, repo.id, recent=2)
    counts = await get_health_snapshot_file_counts(async_session, repo.id, limit=2)

    assert headline.snapshot_count == 4
    assert [s.hotspot_health for s in headline.recent] == [float(c) for c in counts]


async def test_snapshot_file_counts_tolerates_an_unusable_map(
    async_session, tmp_path
) -> None:
    """A malformed blob counts 0, which the caller reads as "no delta"."""
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    await _history(async_session, repo.id, 2)
    rows = (
        await async_session.execute(
            select(HealthSnapshot).order_by(HealthSnapshot.taken_at.asc())
        )
    ).scalars().all()
    rows[0].per_file_scores_json = "{not json"
    rows[1].per_file_scores_json = "[]"
    await async_session.flush()

    assert await get_health_snapshot_file_counts(async_session, repo.id, limit=2) == [0, 0]


async def test_snapshot_file_counts_refuse_a_map_that_is_not_a_map(
    async_session, tmp_path
) -> None:
    """A JSON array parses fine and ``len()``s fine, and means nothing.

    Deliberately stricter than the ``len(json.loads(...))`` this replaced, which
    would have reported a three-element array as three files and published a
    delta off it.
    """
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    await _history(async_session, repo.id, 2)
    rows = (
        await async_session.execute(
            select(HealthSnapshot).order_by(HealthSnapshot.taken_at.asc())
        )
    ).scalars().all()
    rows[0].per_file_scores_json = "[1, 2, 3]"
    rows[1].per_file_scores_json = '"a.py"'
    await async_session.flush()

    assert await get_health_snapshot_file_counts(async_session, repo.id, limit=2) == [0, 0]


async def test_snapshot_file_counts_on_a_repo_with_no_history(
    async_session, tmp_path
) -> None:
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))

    assert await get_health_snapshot_file_counts(async_session, repo.id) == []
