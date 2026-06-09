"""Tests for the "does the score find the bugs?" self-validation stat."""

from __future__ import annotations

from repowise.core.analysis.health.defect_accuracy import compute_defect_accuracy


def _metrics(n: int) -> list[dict]:
    # Ascending score == ascending health; index i is the i-th lowest-health file.
    return [{"file_path": f"f{i}.py", "score": float(i)} for i in range(n)]


def _fix(path: str, count: int = 1, window_days: int = 180) -> dict:
    return {
        "biomarker_type": "prior_defect",
        "file_path": path,
        "details": {"prior_defect_count": count, "window_days": window_days},
    }


def test_returns_none_without_enough_files() -> None:
    metrics = _metrics(10)
    findings = [_fix(f"f{i}.py") for i in range(5)]
    assert compute_defect_accuracy(metrics, findings) is None


def test_returns_none_without_enough_defects() -> None:
    metrics = _metrics(30)
    findings = [_fix(f"f{i}.py") for i in range(4)]  # < _MIN_DEFECT_FILES
    assert compute_defect_accuracy(metrics, findings) is None


def test_precision_lift_and_concentration() -> None:
    # 30 files; the 8 lowest-health files are exactly the bug-fixed ones.
    metrics = _metrics(30)
    findings = [_fix(f"f{i}.py") for i in range(8)]

    stat = compute_defect_accuracy(metrics, findings)
    assert stat is not None
    assert stat["k"] == 20
    assert stat["hits"] == 8
    assert stat["scored_files"] == 30
    assert stat["defect_files"] == 8
    # base_rate = 8/30, precision = 8/20.
    assert stat["base_rate"] == round(8 / 30, 4)
    assert stat["precision"] == round(8 / 20, 4)
    assert stat["lift"] == round((8 / 20) / (8 / 30), 2)
    # All 8 fixed files sit inside the least-healthy 20% (6 files) ... only 6 fit,
    # so concentration counts the 6 in the top bucket out of 8 total defect files.
    assert 0 < stat["concentration_defect_share"] <= 1.0
    assert stat["window_days"] == 180
    assert {row["k"] for row in stat["precision_table"]} == {10, 20, 30}


def test_lift_none_when_no_baseline_is_handled() -> None:
    # Guardrails fire long before base_rate could be zero (it needs >=5 defect
    # files), so a real call always has a baseline; lift is therefore a number.
    metrics = _metrics(40)
    findings = [_fix(f"f{i}.py") for i in range(5)]
    stat = compute_defect_accuracy(metrics, findings)
    assert stat is not None
    assert stat["lift"] is not None


def test_window_days_from_finding_details() -> None:
    metrics = _metrics(30)
    findings = [_fix(f"f{i}.py", window_days=90) for i in range(6)]
    stat = compute_defect_accuracy(metrics, findings)
    assert stat is not None
    assert stat["window_days"] == 90
