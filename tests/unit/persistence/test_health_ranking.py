"""Worst-first ordering of per-file health metrics.

The score clamps at 1.0, so on a real repo dozens of files tie there and a list
sorted on score alone comes back in DB order. Measured on this repo's own
index: 30 files at exactly 1.0, and the file carrying the largest deduction
(12.9) landed at position 27 of a list capped at 20.
"""

from __future__ import annotations

from repowise.core.persistence.crud import (
    get_deduction_by_path,
    get_health_metrics,
    save_health_findings,
    save_health_metrics,
    sort_metrics_worst_first,
    upsert_repository,
)


def _metric(path: str, score: float) -> dict:
    return {
        "file_path": path,
        "score": score,
        "max_ccn": 1,
        "max_nesting": 1,
        "nloc": 10,
        "has_test_file": False,
        "module": "src",
    }


def _finding(path: str, impact: float, name: str = "f") -> dict:
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
    }


async def _seed(async_session, tmp_path, metrics: list[dict], findings: list[dict]):
    repo = await upsert_repository(async_session, name="repo", local_path=str(tmp_path))
    await save_health_metrics(async_session, repo.id, metrics)
    await save_health_findings(async_session, repo.id, findings)
    return repo


async def test_floored_files_rank_by_deduction_not_path(async_session, tmp_path) -> None:
    """The deepest floored file leads, even when its path sorts last."""
    repo = await _seed(
        async_session,
        tmp_path,
        [_metric("a.py", 1.0), _metric("m.py", 1.0), _metric("z.py", 1.0)],
        # Deliberately inverse to path order: sorting on score alone would
        # return a, m, z and bury the worst file.
        [_finding("a.py", 2.0), _finding("m.py", 5.0), _finding("z.py", 9.0)],
    )

    rows = await get_health_metrics(async_session, repo.id)

    assert [m.file_path for m in rows] == ["z.py", "m.py", "a.py"]


async def test_deduction_ties_break_on_path_so_paging_is_stable(async_session, tmp_path) -> None:
    """Equal score and equal deduction still produce one total order.

    Without the trailing key two equal rows could swap between requests, which
    a caller paging by offset reads as a row appearing twice or not at all.
    """
    repo = await _seed(
        async_session,
        tmp_path,
        [_metric("b.py", 1.0), _metric("a.py", 1.0)],
        [_finding("a.py", 3.0), _finding("b.py", 3.0)],
    )

    rows = await get_health_metrics(async_session, repo.id)
    assert [m.file_path for m in rows] == ["a.py", "b.py"]


async def test_file_with_no_findings_sorts_after_a_tied_file_that_has_them(
    async_session, tmp_path
) -> None:
    """A miss in the deduction map is 0.0: a clean file has no magnitude."""
    repo = await _seed(
        async_session,
        tmp_path,
        [_metric("clean.py", 1.0), _metric("deep.py", 1.0)],
        [_finding("deep.py", 4.0)],
    )

    assert [m.file_path for m in await get_health_metrics(async_session, repo.id)] == [
        "deep.py",
        "clean.py",
    ]


async def test_score_still_outranks_deduction(async_session, tmp_path) -> None:
    """Deduction is the tiebreak, not the sort. A worse score always leads."""
    repo = await _seed(
        async_session,
        tmp_path,
        [_metric("low.py", 1.0), _metric("high.py", 6.0)],
        [_finding("low.py", 1.0), _finding("high.py", 40.0)],
    )

    assert [m.file_path for m in await get_health_metrics(async_session, repo.id)] == [
        "low.py",
        "high.py",
    ]


async def test_scoped_read_ranks_and_sums_only_the_requested_paths(
    async_session, tmp_path
) -> None:
    repo = await _seed(
        async_session,
        tmp_path,
        [_metric("a.py", 1.0), _metric("b.py", 1.0), _metric("c.py", 1.0)],
        [_finding("a.py", 2.0), _finding("b.py", 7.0), _finding("c.py", 99.0)],
    )

    rows = await get_health_metrics(async_session, repo.id, file_paths=["a.py", "b.py"])
    assert [m.file_path for m in rows] == ["b.py", "a.py"]

    scoped = await get_deduction_by_path(async_session, repo.id, file_paths=["a.py", "b.py"])
    assert scoped == {"a.py": 2.0, "b.py": 7.0}


async def test_deduction_sums_every_finding_on_a_file(async_session, tmp_path) -> None:
    repo = await _seed(
        async_session,
        tmp_path,
        [_metric("a.py", 1.0)],
        [_finding("a.py", 1.5, "one"), _finding("a.py", 2.25, "two")],
    )

    assert await get_deduction_by_path(async_session, repo.id) == {"a.py": 3.75}


def test_sort_helper_is_pure_and_leaves_its_input_alone() -> None:
    """The MCP tool feeds this rows it also serializes from, unsorted."""

    class _Row:
        def __init__(self, path: str, score: float) -> None:
            self.file_path = path
            self.score = score

    rows = [_Row("a.py", 1.0), _Row("b.py", 1.0)]
    ordered = sort_metrics_worst_first(rows, {"b.py": 5.0})

    assert [r.file_path for r in ordered] == ["b.py", "a.py"]
    assert [r.file_path for r in rows] == ["a.py", "b.py"]
