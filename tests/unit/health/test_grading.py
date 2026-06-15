"""Tests for the health band + distribution "currency" layer.

The cutoffs are frozen (D6: scoring/presentation is frozen) and mirrored in
``packages/types/src/health.ts``; the boundary assertions here are the Python
half of the cross-language parity guard.
"""

from __future__ import annotations

from repowise.core.analysis.health.grading import (
    ALERT_MAX,
    HEALTHY_MIN,
    band_for,
    distribution,
)


def test_cutoffs_are_frozen_defect_backed_values() -> None:
    # If these change, the TS mirror (band-cutoffs test) must change too.
    assert ALERT_MAX == 4.0
    assert HEALTHY_MIN == 8.0


def test_band_for_boundaries() -> None:
    assert band_for(1.0) == "alert"
    assert band_for(3.99) == "alert"
    assert band_for(4.0) == "warning"  # ALERT_MAX inclusive of warning
    assert band_for(6.0) == "warning"
    assert band_for(7.99) == "warning"
    assert band_for(8.0) == "healthy"  # HEALTHY_MIN inclusive of healthy
    assert band_for(10.0) == "healthy"


def test_distribution_empty_repo() -> None:
    dist = distribution([])
    assert dist["total_files"] == 0
    assert dist["total_nloc"] == 0
    for band in ("healthy", "warning", "alert"):
        assert dist["bands"][band] == {"files": 0, "nloc": 0, "pct": 0.0}


def test_distribution_nloc_weighted() -> None:
    # One healthy file with lots of NLOC vs many tiny alert files: the
    # percentage is NLOC-weighted, not file-count-weighted.
    metrics = [
        {"file_path": "big_healthy.py", "score": 9.0, "nloc": 900},
        {"file_path": "a.py", "score": 2.0, "nloc": 50},
        {"file_path": "b.py", "score": 3.0, "nloc": 50},
    ]
    dist = distribution(metrics)
    assert dist["total_files"] == 3
    assert dist["total_nloc"] == 1000
    assert dist["bands"]["healthy"] == {"files": 1, "nloc": 900, "pct": 90.0}
    assert dist["bands"]["alert"] == {"files": 2, "nloc": 100, "pct": 10.0}
    assert dist["bands"]["warning"] == {"files": 0, "nloc": 0, "pct": 0.0}


def test_distribution_floors_zero_nloc_at_one() -> None:
    # A zero-NLOC file still counts once toward its band's weight.
    metrics = [
        {"file_path": "z.py", "score": 9.0, "nloc": 0},
        {"file_path": "w.py", "score": 5.0, "nloc": 0},
    ]
    dist = distribution(metrics)
    assert dist["total_nloc"] == 2
    assert dist["bands"]["healthy"]["nloc"] == 1
    assert dist["bands"]["warning"]["nloc"] == 1


def test_distribution_accepts_objects() -> None:
    class _M:
        def __init__(self, path: str, score: float, nloc: int) -> None:
            self.file_path = path
            self.score = score
            self.nloc = nloc

    dist = distribution([_M("a.py", 9.0, 100), _M("b.py", 2.0, 100)])
    assert dist["bands"]["healthy"]["pct"] == 50.0
    assert dist["bands"]["alert"]["pct"] == 50.0
