"""Tests for DeadCodeReport.confidence_summary and hidden_below_threshold.

Two things the repo owner called out:

1. confidence_summary invariant: high + medium + low == total_findings.
   The buckets partition the *returned* findings, so this must hold for
   any config and any finding mix.

2. Deprecated symbols have confidence=0.3 (analyzer.py:1135), which is
   permanently below the default min_confidence floor of 0.4.  Under the
   default config they are silently dropped; the report must expose how
   many were hidden via hidden_below_threshold so the CLI can print a
   "pass --min-confidence 0.0" hint.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from tests.unit.dead_code._helpers import _build_graph, _old_date

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stale_file_node(name: str, *, symbols: list | None = None) -> dict:
    """Attributes for a file that looks clearly unused (no commits in a year)."""
    return {
        name: {
            "is_entry_point": False,
            "is_test": False,
            "is_api_contract": False,
            "symbol_count": 5,
            "symbols": symbols or [],
        }
    }


def _stale_git_meta(name: str) -> dict:
    return {
        name: {
            "commit_count_90d": 0,
            "last_commit_at": _old_date(days=400),
            "age_days": 400,
            "primary_owner_name": None,
        }
    }


def _deprecated_symbol(name: str) -> dict:
    """A public symbol whose *name* triggers confidence=0.3.

    The analyzer (analyzer.py ``_detect_unused_exports``) keys on the name
    suffix, not on any decorator:

        is_deprecated = any(
            sym_name.endswith(s) for s in ("_DEPRECATED", "_LEGACY", "_COMPAT")
        )

    Pass a name ending in one of those suffixes (e.g. ``process_data_DEPRECATED``)
    to get the 0.3 score.  A ``@deprecated`` decorator is *inert* and is
    intentionally omitted here so a future rename does not silently lose coverage
    while a misleading decorator sits in place.
    """
    return {
        "name": name,
        "kind": "function",
        "visibility": "public",
        "language": "python",
        "start_line": 1,
        "end_line": 10,
    }


# ---------------------------------------------------------------------------
# confidence_summary invariant
# ---------------------------------------------------------------------------


def test_confidence_summary_partitions_total_findings_default():
    """high + medium + low == total_findings under the default floor (0.4).

    The buckets describe what the report *returns*, so they must always sum
    to total_findings — not to some pre-filter count or to anything else.
    """
    # Build a graph with one stale unreachable file (confidence=1.0 → high)
    # and one file with recent commits (confidence=0.4 → medium).
    from datetime import UTC, datetime, timedelta

    g = _build_graph(
        nodes={
            **_stale_file_node("pkg/old.py"),
            "pkg/active.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 3,
                "symbols": [],
            },
        }
    )
    git_meta = {
        **_stale_git_meta("pkg/old.py"),
        "pkg/active.py": {
            "commit_count_90d": 5,
            "last_commit_at": datetime.now(UTC) - timedelta(days=7),
            "age_days": 200,
            "primary_owner_name": None,
        },
    }

    analyzer = DeadCodeAnalyzer(g, git_meta_map=git_meta)
    report = analyzer.analyze({"detect_unused_exports": False, "detect_zombie_packages": False})

    s = report.confidence_summary
    assert s["high"] + s["medium"] + s["low"] == report.total_findings, (
        f"confidence_summary buckets {s} do not sum to total_findings={report.total_findings}"
    )


def test_confidence_summary_partitions_total_findings_low_floor():
    """Invariant holds even when min_confidence=0.0 and low-confidence findings are returned."""
    # A file with a deprecated export has confidence=0.3 → lands in low bucket.
    g = _build_graph(
        nodes={
            **_stale_file_node(
                "pkg/utils.py",
                symbols=[_deprecated_symbol("process_data_DEPRECATED")],
            ),
            "pkg/caller.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 2,
                "symbols": [],
            },
        },
        edges=[("pkg/caller.py", "pkg/utils.py", {"edge_type": "imports"})],
    )
    git_meta = {**_stale_git_meta("pkg/utils.py"), **_stale_git_meta("pkg/caller.py")}

    analyzer = DeadCodeAnalyzer(g, git_meta_map=git_meta)
    report = analyzer.analyze(
        {
            "detect_unused_exports": True,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )

    s = report.confidence_summary
    assert s["high"] + s["medium"] + s["low"] == report.total_findings, (
        f"confidence_summary buckets {s} do not sum to total_findings={report.total_findings}"
    )
    # The deprecated symbol must appear in the low bucket.
    assert s["low"] >= 1


# ---------------------------------------------------------------------------
# hidden_below_threshold — deprecated symbols silently dropped by default floor
# ---------------------------------------------------------------------------


def test_deprecated_symbol_hidden_under_default_floor():
    """A deprecated export (confidence=0.3) is below the 0.4 default floor.

    Under the default config it must NOT appear in the returned findings, but
    hidden_below_threshold must be 1 so the CLI can print the hint.
    """
    g = _build_graph(
        nodes={
            **_stale_file_node(
                "pkg/utils.py",
                symbols=[_deprecated_symbol("process_data_DEPRECATED")],
            ),
            "pkg/caller.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 2,
                "symbols": [],
            },
        },
        edges=[("pkg/caller.py", "pkg/utils.py", {"edge_type": "imports"})],
    )
    git_meta = {**_stale_git_meta("pkg/utils.py"), **_stale_git_meta("pkg/caller.py")}

    analyzer = DeadCodeAnalyzer(g, git_meta_map=git_meta)
    # Default config — min_confidence=0.4 (RISK_CAP_CONFIDENCE)
    report = analyzer.analyze({"detect_zombie_packages": False})

    deprecated_in_findings = [
        f for f in report.findings if f.symbol_name == "process_data_DEPRECATED"
    ]
    assert deprecated_in_findings == [], (
        "Deprecated symbol should not appear in findings under the default floor"
    )
    assert report.hidden_below_threshold >= 1, (
        "hidden_below_threshold must be non-zero when a deprecated symbol was dropped"
    )


def test_deprecated_symbol_visible_with_low_floor():
    """With min_confidence=0.0 the deprecated symbol appears in findings."""
    g = _build_graph(
        nodes={
            **_stale_file_node(
                "pkg/utils.py",
                symbols=[_deprecated_symbol("process_data_DEPRECATED")],
            ),
            "pkg/caller.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 2,
                "symbols": [],
            },
        },
        edges=[("pkg/caller.py", "pkg/utils.py", {"edge_type": "imports"})],
    )
    git_meta = {**_stale_git_meta("pkg/utils.py"), **_stale_git_meta("pkg/caller.py")}

    analyzer = DeadCodeAnalyzer(g, git_meta_map=git_meta)
    report = analyzer.analyze(
        {"detect_zombie_packages": False, "min_confidence": 0.0}
    )

    deprecated_in_findings = [
        f for f in report.findings if f.symbol_name == "process_data_DEPRECATED"
    ]
    assert len(deprecated_in_findings) == 1
    assert deprecated_in_findings[0].confidence == pytest.approx(0.3)
    assert report.hidden_below_threshold == 0
