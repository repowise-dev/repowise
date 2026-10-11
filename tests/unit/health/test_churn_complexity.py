"""Tests for ``health/churn_complexity.py`` -- the churn x complexity join.

The assembler is a pure surfacing layer: it plots only files with recent churn,
never filters on complexity, normalizes the churn percentile to 0-100, and
sorts by the danger product so a capped caller keeps the worst offenders.
"""

from __future__ import annotations

from dataclasses import dataclass

from repowise.core.analysis.health.churn_complexity import (
    ChurnComplexityPoint,
    churn_complexity_points,
)


@dataclass
class _Metric:
    """Stub mirroring the HealthFileMetric fields the quadrant reads."""

    file_path: str
    score: float = 8.0
    max_ccn: int = 5
    nloc: int = 100


@dataclass
class _Git:
    """Stub mirroring the GitMetadata fields the quadrant reads."""

    commit_count_90d: int | None = 0
    churn_percentile: float | None = None


def test_empty_inputs_yield_no_points():
    assert churn_complexity_points([], {}) == []


def test_file_with_no_recent_churn_is_omitted():
    # commit_count_90d == 0 -> nothing to say on the churn axis -> dropped.
    metrics = [_Metric("a.py")]
    points = churn_complexity_points(metrics, {"a.py": _Git(commit_count_90d=0)})
    assert points == []


def test_file_with_no_git_meta_is_omitted():
    points = churn_complexity_points([_Metric("a.py")], {})
    assert points == []


def test_point_carries_all_axes():
    metrics = [_Metric("a.py", score=3.4, max_ccn=22, nloc=420)]
    git = {"a.py": _Git(commit_count_90d=14, churn_percentile=0.92)}
    points = churn_complexity_points(metrics, git)
    assert len(points) == 1
    p = points[0]
    assert isinstance(p, ChurnComplexityPoint)
    assert p.file_path == "a.py"
    assert p.commit_count_90d == 14
    assert p.max_ccn == 22
    assert p.nloc == 420
    assert p.score == 3.4
    assert p.churn_percentile == 92.0  # 0-1 stored -> 0-100 surfaced


def test_low_complexity_high_churn_file_is_kept():
    # Complexity is never a filter: a churny-but-simple file is a valid
    # bottom-right signal, not noise.
    metrics = [_Metric("simple.py", max_ccn=1)]
    git = {"simple.py": _Git(commit_count_90d=30)}
    points = churn_complexity_points(metrics, git)
    assert len(points) == 1
    assert points[0].max_ccn == 1


def test_sorted_by_danger_product_descending():
    metrics = [
        _Metric("low.py", max_ccn=2),
        _Metric("hot.py", max_ccn=40),
        _Metric("mid.py", max_ccn=10),
    ]
    git = {
        "low.py": _Git(commit_count_90d=3),  # 6
        "hot.py": _Git(commit_count_90d=20),  # 800
        "mid.py": _Git(commit_count_90d=10),  # 100
    }
    points = churn_complexity_points(metrics, git)
    assert [p.file_path for p in points] == ["hot.py", "mid.py", "low.py"]


def test_churn_percentile_tolerates_already_scaled_value():
    metrics = [_Metric("a.py")]
    git = {"a.py": _Git(commit_count_90d=5, churn_percentile=87.0)}
    points = churn_complexity_points(metrics, git)
    assert points[0].churn_percentile == 87.0


def test_missing_churn_percentile_defaults_to_zero():
    metrics = [_Metric("a.py")]
    git = {"a.py": _Git(commit_count_90d=5, churn_percentile=None)}
    points = churn_complexity_points(metrics, git)
    assert points[0].churn_percentile == 0.0
