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
    # High confidence + no dynamic pattern -> still a review candidate: a whole
    # file is never deletion-ready (REVIEW_ONLY_KINDS).
    assert by_path["pkg/old_unused.py"].confidence >= 0.9
    assert by_path["pkg/old_unused.py"].safe_to_delete is False
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
                "symbol_count": 10,
                "symbols": [],
            },
            "pkg/dead2.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 20,
                "symbols": [],
            },
            "pkg/alive.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 15,  # NOT safe
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

    # Line counts come from the source, not from symbol_count.
    source_map = {
        "pkg/dead1.py": b"x = 1\n" * 100,
        "pkg/dead2.py": b"x = 1\n" * 200,
        "pkg/alive.py": b"x = 1\n" * 150,
    }
    analyzer = DeadCodeAnalyzer(g, git_meta_map=git_meta, source_map=source_map)
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
    # Whole files are review-only (REVIEW_ONLY_KINDS), so none is counted as
    # deletable, however stale.
    assert safe_findings == []
    assert report.deletable_lines == 0
