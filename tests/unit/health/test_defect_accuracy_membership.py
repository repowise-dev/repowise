"""Defect accuracy measures the list it validates, not a different one.

The stat ranks files worst-first and asks how many of the top K were recently
bug-fixed. It used to sort on the clamped score alone. The score floors, so the
tied band came out in whatever order the rows arrived in, and the top K it
scored could name different files than the ``worst_files`` list published
beside it — a self-validation of a ranking nobody was shown.
"""

from __future__ import annotations

from repowise.core.analysis.health.defect_accuracy import compute_defect_accuracy
from repowise.core.analysis.health.ranking import deduction_by_path, sort_metrics_worst_first
from repowise.core.analysis.health.scoring import SCORE_FLOOR

_K = 5
_FLOORED = 12  # enough tied files that a score-only sort cannot resolve them


def _metrics() -> list[dict]:
    """25 scored files: a floor-tied band, then healthy filler."""
    floored = [
        {"file_path": f"floor_{i:02d}.py", "score": SCORE_FLOOR, "nloc": 100}
        for i in range(_FLOORED)
    ]
    filler = [
        {"file_path": f"ok_{i:02d}.py", "score": 9.0, "nloc": 100} for i in range(25 - _FLOORED)
    ]
    return floored + filler


def _findings() -> list[dict]:
    """Depth findings for the tied band, plus the bug-fix labels.

    Depth runs *opposite* to path order, so a comparator that ignores it and
    falls back to the path picks the shallowest files, and one that honours it
    picks the deepest. The bug-fixed files are the deep ones.
    """
    rows: list[dict] = []
    for i in range(_FLOORED):
        rows.append(
            {
                "file_path": f"floor_{i:02d}.py",
                "biomarker_type": "god_class",
                "health_impact": 9.0 + i,
            }
        )
    for i in range(_FLOORED - 5, _FLOORED):
        rows.append(
            {
                "file_path": f"floor_{i:02d}.py",
                "biomarker_type": "prior_defect",
                "health_impact": 0.1,
                "details": {"prior_defect_count": 3, "window_days": 90},
            }
        )
    return rows


def _stat() -> dict:
    findings = _findings()
    stat = compute_defect_accuracy(
        _metrics(),
        [f for f in findings if f["biomarker_type"] == "prior_defect"],
        k=_K,
        deductions=deduction_by_path(findings),
    )
    assert stat is not None
    return stat


def test_flagged_files_are_the_canonical_worst_files() -> None:
    findings = _findings()
    canonical = sort_metrics_worst_first(_metrics(), deduction_by_path(findings))
    flagged = [row["file_path"] for row in _stat()["flagged_files"]]
    assert flagged == [row["file_path"] for row in canonical[:_K]]


def test_precision_counts_hits_in_that_same_membership() -> None:
    stat = _stat()
    hits = sum(1 for row in stat["flagged_files"] if row["recent_fixes"] > 0)
    assert stat["hits"] == hits
    assert stat["precision"] == round(hits / stat["k"], 4)


def test_depth_decides_the_floor_tied_band_not_path_order() -> None:
    """Every flagged file is one of the deepest, which is where the fixes are."""
    flagged = {row["file_path"] for row in _stat()["flagged_files"]}
    deepest = {f"floor_{i:02d}.py" for i in range(_FLOORED - _K, _FLOORED)}
    assert flagged == deepest
    assert _stat()["hits"] == _K


def test_concentration_shares_the_same_ranking() -> None:
    stat = _stat()
    # 20% of 25 files is the worst five, which is exactly K here, so the
    # concentration hit count and the precision hit count come from one list.
    assert stat["concentration_file_fraction"] == 0.20
    assert stat["concentration_defect_share"] == round(stat["hits"] / stat["defect_files"], 4)


def test_the_ranking_is_independent_of_the_order_rows_arrive_in() -> None:
    findings = _findings()
    deductions = deduction_by_path(findings)
    labels = [f for f in findings if f["biomarker_type"] == "prior_defect"]
    forward = compute_defect_accuracy(_metrics(), labels, k=_K, deductions=deductions)
    reversed_rows = compute_defect_accuracy(
        list(reversed(_metrics())), labels, k=_K, deductions=deductions
    )
    assert forward == reversed_rows


def test_window_and_counts_still_come_from_stored_details_json() -> None:
    """The label rows reach the server as ORM rows carrying ``details_json``."""
    labels = [
        {
            "file_path": f"floor_{i:02d}.py",
            "biomarker_type": "prior_defect",
            "details_json": '{"prior_defect_count": 4, "window_days": 45}',
        }
        for i in range(_FLOORED - 5, _FLOORED)
    ]
    stat = compute_defect_accuracy(_metrics(), labels, k=_K)
    assert stat is not None
    assert stat["window_days"] == 45
    assert all(row["recent_fixes"] in (0, 4) for row in stat["flagged_files"])
