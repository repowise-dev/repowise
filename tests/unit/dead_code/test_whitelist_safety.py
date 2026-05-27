"""Unit tests for DeadCodeAnalyzer."""

from __future__ import annotations

from datetime import timedelta

from repowise.core.analysis.dead_code import (
    DeadCodeAnalyzer,
)
from tests.unit.dead_code._helpers import _build_graph, _now, _old_date


def test_whitelist_respected():
    """A file in the whitelist should NOT be flagged even if it is unreachable."""
    g = _build_graph(
        nodes={
            "pkg/legacy.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 20,
                "symbols": [],
            },
        },
    )

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unused_exports": False,
            "detect_zombie_packages": False,
            "whitelist": ["pkg/legacy.py"],
        }
    )

    assert all(f.file_path != "pkg/legacy.py" for f in report.findings)


def test_safe_to_delete_conservative():
    """safe_to_delete is True only when confidence >= 0.7 AND file does not match dynamic patterns."""
    g = _build_graph(
        nodes={
            # High confidence, no dynamic pattern match -> safe
            "pkg/old_unused.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 5,
                "symbols": [],
            },
            # High confidence, but file stem matches *Handler -> NOT safe
            "pkg/RequestHandler.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 5,
                "symbols": [],
            },
            # Low confidence (recently touched) -> NOT safe
            "pkg/fresh.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 5,
                "symbols": [],
            },
        },
    )

    git_meta = {
        "pkg/old_unused.py": {
            "commit_count_90d": 0,
            "last_commit_at": _old_date(days=365),
            "age_days": 500,
        },
        "pkg/RequestHandler.py": {
            "commit_count_90d": 0,
            "last_commit_at": _old_date(days=365),
            "age_days": 500,
        },
        "pkg/fresh.py": {
            "commit_count_90d": 5,
            "last_commit_at": _now() - timedelta(days=3),
            "age_days": 60,
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

    by_path = {f.file_path: f for f in report.findings}
    # High confidence + no dynamic pattern -> safe
    assert by_path["pkg/old_unused.py"].safe_to_delete is True
    # High confidence but matches *Handler -> not safe
    assert by_path["pkg/RequestHandler.py"].safe_to_delete is False
    # Low confidence (0.4) -> not safe
    assert by_path["pkg/fresh.py"].safe_to_delete is False


def test_report_deletable_lines_sum():
    """report.deletable_lines should equal the sum of lines for safe_to_delete findings."""
    g = _build_graph(
        nodes={
            "pkg/dead1.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 10,  # lines = 10 * 10 = 100
                "symbols": [],
            },
            "pkg/dead2.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 20,  # lines = 20 * 10 = 200
                "symbols": [],
            },
            "pkg/alive.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 15,  # lines = 15 * 10 = 150, but NOT safe
                "symbols": [],
            },
        },
    )

    git_meta = {
        "pkg/dead1.py": {
            "commit_count_90d": 0,
            "last_commit_at": _old_date(days=365),
            "age_days": 400,
        },
        "pkg/dead2.py": {
            "commit_count_90d": 0,
            "last_commit_at": _old_date(days=365),
            "age_days": 400,
        },
        # Recently touched -> confidence 0.4, safe_to_delete=False
        "pkg/alive.py": {
            "commit_count_90d": 5,
            "last_commit_at": _now() - timedelta(days=2),
            "age_days": 60,
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

    safe_findings = [f for f in report.findings if f.safe_to_delete]
    expected_lines = sum(f.lines for f in safe_findings)
    assert report.deletable_lines == expected_lines
    # Verify that the safe findings include the two stale files
    safe_paths = {f.file_path for f in safe_findings}
    assert "pkg/dead1.py" in safe_paths
    assert "pkg/dead2.py" in safe_paths
    assert "pkg/alive.py" not in safe_paths
    # Verify the actual sum
    assert report.deletable_lines == 100 + 200
