"""A file with no git row is scored as unknown, not as untouched.

The confidence ladder's ``commit_count_90d == 0`` rung is for a file git has
data on. A file absent from the map used to fall through to that rung at 0.7,
which is the deletion-ready threshold, so the weakest evidence the analyzer
sees produced a verdict as strong as three quiet months.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.analysis.dead_code.risk_factors import (
    NO_GIT_SIGNAL_CONFIDENCE,
    SAFE_CONFIDENCE_THRESHOLD,
)
from tests.unit.dead_code._helpers import _build_graph, _now

_NODE = {
    "is_entry_point": False,
    "is_test": False,
    "is_api_contract": False,
    "symbol_count": 5,
    "symbols": [],
}
_CONFIG = {
    "detect_unused_exports": False,
    "detect_zombie_packages": False,
    "min_confidence": 0.0,
}


def _finding(git_meta_map: dict) -> object:
    g = _build_graph(nodes={"pkg/orphan.py": dict(_NODE)})
    report = DeadCodeAnalyzer(g, git_meta_map=git_meta_map).analyze(_CONFIG)
    return next(f for f in report.findings if f.file_path == "pkg/orphan.py")


def test_missing_row_is_not_deletion_ready() -> None:
    finding = _finding({})
    assert finding.confidence == pytest.approx(NO_GIT_SIGNAL_CONFIDENCE)
    assert finding.confidence < SAFE_CONFIDENCE_THRESHOLD
    assert not finding.safe_to_delete
    assert "No git history recorded for this file" in finding.evidence
    assert "No commits in last 90 days" not in finding.evidence


def test_empty_row_reads_the_same_as_a_missing_one() -> None:
    finding = _finding({"pkg/orphan.py": {}})
    assert finding.confidence == pytest.approx(NO_GIT_SIGNAL_CONFIDENCE)
    assert not finding.safe_to_delete


def test_a_row_with_no_recent_commits_keeps_its_rung() -> None:
    """The fix narrows one rung; a file git confirms quiet still scores it."""
    finding = _finding(
        {
            "pkg/orphan.py": {
                "commit_count_90d": 0,
                "last_commit_at": _now() - timedelta(days=400),
                "age_days": 900,
            }
        }
    )
    assert finding.confidence == pytest.approx(1.0)
    assert finding.safe_to_delete
