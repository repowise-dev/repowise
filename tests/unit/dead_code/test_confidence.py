"""Unit tests for DeadCodeAnalyzer."""

from __future__ import annotations

from datetime import timedelta

import pytest

from repowise.core.analysis.dead_code import (
    DeadCodeAnalyzer,
)
from tests.unit.dead_code._helpers import _build_graph, _now, _old_date


def test_confidence_low_for_recent_files():
    """Unreachable file with commit_count_90d > 0 should have confidence 0.4."""
    g = _build_graph(
        nodes={
            "pkg/recent.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 5,
                "symbols": [],
            },
        },
    )

    git_meta = {
        "pkg/recent.py": {
            "commit_count_90d": 3,
            "last_commit_at": _now() - timedelta(days=10),
            "age_days": 100,
            "primary_owner_name": "dev@example.com",
        },
    }

    analyzer = DeadCodeAnalyzer(g, git_meta_map=git_meta)
    report = analyzer.analyze(
        {
            "detect_unused_exports": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )

    findings = [f for f in report.findings if f.file_path == "pkg/recent.py"]
    assert len(findings) == 1
    assert findings[0].confidence == pytest.approx(0.4)


def test_confidence_high_for_stale_unreachable():
    """Unreachable file with no commits in 90d and last commit > 6 months ago -> confidence 1.0."""
    g = _build_graph(
        nodes={
            "pkg/stale.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 5,
                "symbols": [],
            },
        },
    )

    git_meta = {
        "pkg/stale.py": {
            "commit_count_90d": 0,
            "last_commit_at": _old_date(days=365),
            "age_days": 730,
            "primary_owner_name": "dev@example.com",
        },
    }

    analyzer = DeadCodeAnalyzer(g, git_meta_map=git_meta)
    report = analyzer.analyze(
        {
            "detect_unused_exports": False,
            "detect_zombie_packages": False,
        }
    )

    findings = [f for f in report.findings if f.file_path == "pkg/stale.py"]
    assert len(findings) == 1
    assert findings[0].confidence == pytest.approx(1.0)
