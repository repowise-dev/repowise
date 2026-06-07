"""Calibration of PRBlastRadiusAnalyzer._compute_overall_risk.

Regression: the old ``min(raw * 100, 10)`` normalisation scaled the 0-1
breadth bonus to 0-20 points, so any PR with >=20 transitive dependents â€”
i.e. any PR touching a hotspot â€” reported exactly 10.0. The score must
discriminate between small clean diffs, moderate diffs, and hotspot-heavy
diffs, and only approach 10 asymptotically.
"""

from __future__ import annotations

from repowise.core.analysis.pr_blast import PRBlastRadiusAnalyzer


def _direct(*scores: float) -> list[dict]:
    return [{"risk_score": s} for s in scores]


def _transitive(n: int) -> list[dict]:
    return [{"path": f"f{i}.py"} for i in range(n)]


class TestOverallRiskCalibration:
    def test_empty_is_zero(self):
        assert PRBlastRadiusAnalyzer._compute_overall_risk([], []) == 0.0

    def test_small_clean_diff_scores_low(self):
        # Three low-centrality, low-churn files, narrow blast.
        score = PRBlastRadiusAnalyzer._compute_overall_risk(
            _direct(0.002, 0.001, 0.003), _transitive(2)
        )
        assert 0.0 < score < 2.0

    def test_hotspot_diff_does_not_saturate(self):
        # One genuine hotspot (pagerank*(1+temporal) ~ 0.2) plus wide blast:
        # previously this pinned at exactly 10.0.
        score = PRBlastRadiusAnalyzer._compute_overall_risk(
            _direct(0.2, 0.01, 0.005), _transitive(40)
        )
        assert score < 10.0
        assert score > 3.0

    def test_breadth_alone_cannot_max_the_score(self):
        # Regression core: >=20 transitive dependents alone used to add 20
        # points. Breadth now contributes at most 2.
        score = PRBlastRadiusAnalyzer._compute_overall_risk(_direct(0.001), _transitive(100))
        assert score < 4.0

    def test_monotonic_in_file_risk(self):
        low = PRBlastRadiusAnalyzer._compute_overall_risk(_direct(0.01), _transitive(5))
        mid = PRBlastRadiusAnalyzer._compute_overall_risk(_direct(0.05), _transitive(5))
        high = PRBlastRadiusAnalyzer._compute_overall_risk(_direct(0.3), _transitive(5))
        assert low < mid < high

    def test_monotonic_in_breadth(self):
        narrow = PRBlastRadiusAnalyzer._compute_overall_risk(_direct(0.05), _transitive(0))
        wide = PRBlastRadiusAnalyzer._compute_overall_risk(_direct(0.05), _transitive(40))
        assert narrow < wide
        assert round(wide - narrow, 6) <= 2.0  # breadth term is capped at 2 points

    def test_never_exceeds_ten(self):
        score = PRBlastRadiusAnalyzer._compute_overall_risk(
            _direct(5.0, 5.0, 5.0), _transitive(500)
        )
        assert score <= 10.0
