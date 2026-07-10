"""Regression: churn-risk reason wording for extreme relative churn.

An above-100% churn/NLOC ratio used to render with ``:.0%`` and print
nonsense like "15475%". It must render as a multiplier ("N.Nx the file's
size") instead. The numeric score fields must be untouched.
"""

from __future__ import annotations

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.churn_risk import ChurnRiskDetector


def _ctx(meta: dict, *, nloc: int = 120) -> FileContext:
    return FileContext(
        file_path="src/payments.py",
        language="python",
        nloc=nloc,
        has_test_file=False,
        module=None,
        function_metrics={},
        git_meta=meta,
        dependents_count=4,
        pagerank_score=0.0,
    )


def test_extreme_churn_renders_as_multiplier_not_percentage():
    # nloc=1; 155 lines churned → relative_churn 155.0 → old text "15500%".
    meta = {
        "commit_count_90d": 20,
        "churn_percentile": 0.99,
        "lines_added_90d": 100,
        "lines_deleted_90d": 55,
        "is_hotspot": True,
    }
    out = ChurnRiskDetector().detect(_ctx(meta, nloc=1))
    assert len(out) == 1
    reason = out[0].reason
    assert "155.0x the file's size" in reason
    assert "%" not in reason.split("top")[0]  # no percentage before the percentile clause
    # Score-bearing detail fields unchanged.
    assert out[0].details["relative_churn"] == 155.0


def test_churn_multiplier_used_at_and_above_one():
    # relative_churn exactly 1.5 (100 + 80 over 120 nloc).
    meta = {
        "commit_count_90d": 8,
        "churn_percentile": 0.9,
        "lines_added_90d": 100,
        "lines_deleted_90d": 80,
        "is_hotspot": False,
    }
    out = ChurnRiskDetector().detect(_ctx(meta))
    assert len(out) == 1
    assert "1.5x the file's size" in out[0].reason
    # No four-plus-digit percentage anywhere.
    assert "15475%" not in out[0].reason
