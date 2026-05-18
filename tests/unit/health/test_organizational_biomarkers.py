"""Developer Congestion + Knowledge Loss biomarker tests."""

from __future__ import annotations

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.developer_congestion import (
    DeveloperCongestionDetector,
)
from repowise.core.analysis.health.biomarkers.knowledge_loss import (
    KnowledgeLossDetector,
)


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
    }
    out = KnowledgeLossDetector().detect(_ctx(meta))
    assert len(out) == 1


def test_knowledge_loss_skips_when_bus_factor_healthy():
    meta = {
        "bus_factor": 4,
        "primary_owner_name": "Alice",
        "recent_owner_name": "Bob",
        "recent_owner_commit_pct": 0.1,
    }
    assert KnowledgeLossDetector().detect(_ctx(meta)) == []


def test_knowledge_loss_skips_without_owner_data():
    assert KnowledgeLossDetector().detect(_ctx({})) == []
