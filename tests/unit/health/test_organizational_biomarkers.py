"""Organizational biomarker tests: Developer Congestion, Knowledge Loss,
Ownership Risk, Churn Risk, Change Entropy, Co-change Scatter."""

from __future__ import annotations

import json

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.change_entropy import (
    ChangeEntropyDetector,
)
from repowise.core.analysis.health.biomarkers.churn_risk import ChurnRiskDetector
from repowise.core.analysis.health.biomarkers.co_change_scatter import (
    CoChangeScatterDetector,
)
from repowise.core.analysis.health.biomarkers.developer_congestion import (
    DeveloperCongestionDetector,
)
from repowise.core.analysis.health.biomarkers.knowledge_loss import (
    KnowledgeLossDetector,
)
from repowise.core.analysis.health.biomarkers.ownership_risk import (
    OwnershipRiskDetector,
)


def _authors(*pairs: tuple[str, int]) -> str:
    return json.dumps([{"name": n, "email": f"{n}@x", "commit_count": c} for n, c in pairs])


def _ctx(meta: dict) -> FileContext:
    return FileContext(
        file_path="src/payments.py",
        language="python",
        nloc=120,
        has_test_file=False,
        module=None,
        function_metrics={},
        git_meta=meta,
        dependents_count=4,
        pagerank_score=0.0,
    )


# ---- developer_congestion ------------------------------------------------


def test_developer_congestion_fires_on_crowded_hotspot():
    meta = {
        "contributor_count": 12,
        "commit_count_90d": 25,
        "primary_owner_commit_pct": 0.25,
        "primary_owner_name": "Alice",
    }
    out = DeveloperCongestionDetector().detect(_ctx(meta))
    assert len(out) == 1
    assert out[0].severity == "high"
    assert out[0].details["contributor_count"] == 12


def test_developer_congestion_normalizes_percent_form():
    meta = {
        "contributor_count": 6,
        "commit_count_90d": 8,
        "primary_owner_commit_pct": 30.0,  # percent form
    }
    out = DeveloperCongestionDetector().detect(_ctx(meta))
    assert out
    assert out[0].details["primary_owner_share"] == 0.3


def test_developer_congestion_skips_with_clear_owner():
    meta = {
        "contributor_count": 8,
        "commit_count_90d": 15,
        "primary_owner_commit_pct": 0.65,
    }
    assert DeveloperCongestionDetector().detect(_ctx(meta)) == []


def test_developer_congestion_skips_low_activity():
    meta = {
        "contributor_count": 8,
        "commit_count_90d": 2,
        "primary_owner_commit_pct": 0.2,
    }
    assert DeveloperCongestionDetector().detect(_ctx(meta)) == []


# ---- knowledge_loss ------------------------------------------------------


def test_knowledge_loss_fires_when_primary_owner_left():
    meta = {
        "bus_factor": 1,
        "primary_owner_name": "Alice",
        "recent_owner_name": "Bob",
        "recent_owner_commit_pct": 0.8,
        "is_hotspot": True,
    }
    out = KnowledgeLossDetector().detect(_ctx(meta))
    assert len(out) == 1
    assert out[0].severity == "high"  # hotspot escalation
    assert out[0].details["primary_owner"] == "Alice"


def test_knowledge_loss_fires_when_recent_share_collapses():
    meta = {
        "bus_factor": 1,
        "primary_owner_name": "Alice",
        "recent_owner_name": "Alice",
        "recent_owner_commit_pct": 0.05,
        "is_hotspot": False,
        "commit_count_90d": 3,  # still active → passes the activity gate
    }
    out = KnowledgeLossDetector().detect(_ctx(meta))
    assert len(out) == 1


def test_knowledge_loss_skips_abandoned_stable_file():
    """Regression: an abandoned-but-stable file is low risk (survivor
    effect) and must no longer fire after the activity gate."""
    meta = {
        "bus_factor": 1,
        "primary_owner_name": "Alice",
        "recent_owner_name": "Bob",
        "recent_owner_commit_pct": 0.8,
        "is_hotspot": False,
        "commit_count_90d": 0,
    }
    assert KnowledgeLossDetector().detect(_ctx(meta)) == []


def test_knowledge_loss_skips_when_is_stable():
    meta = {
        "bus_factor": 1,
        "primary_owner_name": "Alice",
        "recent_owner_name": "Bob",
        "recent_owner_commit_pct": 0.8,
        "is_hotspot": True,  # even a hotspot must not fire when stable
        "is_stable": True,
    }
    assert KnowledgeLossDetector().detect(_ctx(meta)) == []


def test_knowledge_loss_skips_when_bus_factor_healthy():
    meta = {
        "bus_factor": 4,
        "primary_owner_name": "Alice",
        "recent_owner_name": "Bob",
        "recent_owner_commit_pct": 0.1,
    }
    assert KnowledgeLossDetector().detect(_ctx(meta)) == []


def test_knowledge_loss_skips_without_owner_data():
    assert KnowledgeLossDetector().detect(_ctx({"commit_count_90d": 3})) == []


# ---- ownership_risk ------------------------------------------------------


def test_ownership_risk_fires_on_fragmented_ownership():
    # Owner holds 50/55; five drive-by authors each <5% of commits.
    meta = {
        "top_authors_json": _authors(
            ("Alice", 50), ("b", 1), ("c", 1), ("d", 1), ("e", 1), ("f", 1)
        ),
        "is_hotspot": False,
    }
    out = OwnershipRiskDetector().detect(_ctx(meta))
    assert len(out) == 1
    assert out[0].severity == "high"  # minor_contributors >= 5
    assert out[0].details["minor_contributors"] == 5
    assert out[0].details["total_commits"] == 55


def test_ownership_risk_escalates_to_critical_on_hotspot():
    meta = {
        "top_authors_json": _authors(
            ("Alice", 60),
            ("b", 1),
            ("c", 1),
            ("d", 1),
            ("e", 1),
            ("f", 1),
            ("g", 1),
        ),
        "is_hotspot": True,
    }
    out = OwnershipRiskDetector().detect(_ctx(meta))
    assert out
    assert out[0].severity == "critical"  # >= 6 minor + hotspot


def test_ownership_risk_skips_clear_single_owner():
    # One dominant owner, one substantial co-owner, no minor contributors.
    meta = {"top_authors_json": _authors(("Alice", 30), ("Bob", 10))}
    assert OwnershipRiskDetector().detect(_ctx(meta)) == []


def test_ownership_risk_skips_low_commit_files():
    meta = {"top_authors_json": _authors(("Alice", 2), ("Bob", 1))}
    assert OwnershipRiskDetector().detect(_ctx(meta)) == []


# ---- churn_risk ----------------------------------------------------------


def test_churn_risk_fires_on_high_relative_churn():
    # nloc=120 (from _ctx); 180 lines churned in 90d → relative_churn 1.5.
    meta = {
        "commit_count_90d": 8,
        "churn_percentile": 0.9,
        "lines_added_90d": 100,
        "lines_deleted_90d": 80,
        "is_hotspot": False,
    }
    out = ChurnRiskDetector().detect(_ctx(meta))
    assert len(out) == 1
    assert out[0].severity == "medium"  # 1.5 <= rc < 2.5
    assert out[0].details["relative_churn"] == 1.5


def test_churn_risk_escalates_to_critical_on_hotspot():
    meta = {
        "commit_count_90d": 12,
        "churn_percentile": 0.95,
        "lines_added_90d": 400,
        "lines_deleted_90d": 200,  # 600/120 = 5.0
        "is_hotspot": True,
    }
    out = ChurnRiskDetector().detect(_ctx(meta))
    assert out
    assert out[0].severity == "critical"  # rc >= 4 and hotspot


def test_churn_risk_skips_low_percentile():
    meta = {
        "commit_count_90d": 8,
        "churn_percentile": 0.4,  # below the 0.75 floor
        "lines_added_90d": 100,
        "lines_deleted_90d": 80,
    }
    assert ChurnRiskDetector().detect(_ctx(meta)) == []


def test_churn_risk_skips_size_proportionate_churn():
    # High percentile + active, but churn is small relative to the file.
    meta = {
        "commit_count_90d": 8,
        "churn_percentile": 0.9,
        "lines_added_90d": 20,
        "lines_deleted_90d": 10,  # 30/120 = 0.25 < 1.0
    }
    assert ChurnRiskDetector().detect(_ctx(meta)) == []


# ---- change_entropy ------------------------------------------------------


def test_change_entropy_fires_on_high_percentile():
    meta = {
        "change_entropy": 1.4,
        "change_entropy_pct": 0.88,
        "commit_count_90d": 6,
        "is_hotspot": False,
    }
    out = ChangeEntropyDetector().detect(_ctx(meta))
    assert len(out) == 1
    assert out[0].severity == "medium"  # 0.80 <= pct < 0.90
    assert out[0].details["change_entropy_pct"] == 0.88


def test_change_entropy_escalates_to_critical_on_hotspot():
    meta = {
        "change_entropy": 3.2,
        "change_entropy_pct": 0.97,
        "commit_count_90d": 9,
        "is_hotspot": True,
    }
    out = ChangeEntropyDetector().detect(_ctx(meta))
    assert out
    assert out[0].severity == "critical"  # pct >= 0.95 and hotspot


def test_change_entropy_skips_when_below_percentile():
    meta = {
        "change_entropy": 0.6,
        "change_entropy_pct": 0.5,  # below the 0.80 floor
        "commit_count_90d": 6,
    }
    assert ChangeEntropyDetector().detect(_ctx(meta)) == []


def test_change_entropy_skips_low_activity():
    meta = {
        "change_entropy": 2.0,
        "change_entropy_pct": 0.95,
        "commit_count_90d": 2,  # below the 3-commit activity gate
    }
    assert ChangeEntropyDetector().detect(_ctx(meta)) == []


def test_change_entropy_silent_without_signal():
    # ESSENTIAL tier / file that never co-changed → no entropy, no firing
    # even though the (defaulted) percentile field is present.
    meta = {"change_entropy": 0.0, "change_entropy_pct": 0.0, "commit_count_90d": 8}
    assert ChangeEntropyDetector().detect(_ctx(meta)) == []


# ---- co_change_scatter ---------------------------------------------------


def _partners(*pairs: tuple[str, float]) -> str:
    return json.dumps([{"file_path": p, "co_change_count": c} for p, c in pairs])


def test_co_change_scatter_fires_on_broad_coupling():
    meta = {
        "co_change_partners_json": _partners(*[(f"m{i}.py", 3.0) for i in range(9)]),
        "commit_count_90d": 5,
    }
    out = CoChangeScatterDetector().detect(_ctx(meta))
    assert len(out) == 1
    assert out[0].severity == "medium"  # 8 <= scatter < 15
    assert out[0].details["scatter"] == 9


def test_co_change_scatter_high_severity_on_heavy_coupling():
    meta = {
        "co_change_partners_json": _partners(*[(f"m{i}.py", 2.5) for i in range(16)]),
        "commit_count_90d": 7,
    }
    out = CoChangeScatterDetector().detect(_ctx(meta))
    assert out
    assert out[0].severity == "high"  # scatter >= 15


def test_co_change_scatter_ignores_weak_partners():
    # Many partners, but all below the 2.0 weight floor → scatter == 0.
    meta = {
        "co_change_partners_json": _partners(*[(f"m{i}.py", 1.0) for i in range(12)]),
        "commit_count_90d": 5,
    }
    assert CoChangeScatterDetector().detect(_ctx(meta)) == []


def test_co_change_scatter_skips_low_activity():
    meta = {
        "co_change_partners_json": _partners(*[(f"m{i}.py", 3.0) for i in range(10)]),
        "commit_count_90d": 1,
    }
    assert CoChangeScatterDetector().detect(_ctx(meta)) == []


def test_co_change_scatter_silent_on_essential_tier():
    # Empty partner list (ESSENTIAL git tier) → no signal.
    assert CoChangeScatterDetector().detect(_ctx({"commit_count_90d": 8})) == []
