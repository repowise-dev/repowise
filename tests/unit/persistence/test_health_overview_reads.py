"""The two reads ``/health/overview`` was paying for twice.

``get_health_summary`` internally loads both the metrics and the findings
tables. The overview route had already loaded both for its own payload, so
every request scanned each table twice. ``metrics=`` closed half of that;
these pin the ``findings=`` half and the snapshot-scalar read that replaced a
full-entity load of every retained per-file score map.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from repowise.core.persistence.crud import (
    get_health_snapshot_headline,
    get_health_summary,
    save_health_findings,
    save_health_metrics,
    save_health_snapshot,
    upsert_repository,
)


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

    assert await get_health_snapshot_headline(async_session, repo.id) == (None, None, 0)
