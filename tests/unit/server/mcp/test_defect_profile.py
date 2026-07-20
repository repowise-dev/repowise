"""get_risk's defect_profile and the fix-history it replaces the keyword scan with.

Two contracts here. The block is silent when a file has no counted fixes, so an
index without the fix-event rollup grows no noise (the FileSignalsPanel
convention every surface in this feature follows). And nothing in it names a
commit: file-level SZZ measured 74.5% precision against the frozen judgments,
which counts fixes honestly and accuses one dishonestly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from repowise.server.mcp_server.tool_risk.assessment import (
    _classify_risk_type,
    _defect_profile,
    _fix_clause,
    _top_fix_symbols,
    fix_annotation,
)


def _meta(**overrides) -> SimpleNamespace:
    base = {
        "prior_defect_count": 0,
        "bug_magnet": False,
        "last_fix_at": None,
        "fix_symbol_counts_json": None,
        "churn_percentile": 0.1,
        "bus_factor": 3,
        "commit_count_total": 10,
        "is_hotspot": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_no_fix_history_is_silent():
    """A clean file adds nothing to the response, rather than a block of zeros."""
    assert _defect_profile(_meta()) is None


def test_counted_fixes_render_with_recency_and_symbols():
    profile = _defect_profile(
        _meta(
            prior_defect_count=5,
            bug_magnet=True,
            last_fix_at=datetime.now(UTC) - timedelta(days=14),
            fix_symbol_counts_json=json.dumps(
                {
                    "src/pipeline.py::run": 4,
                    "src/pipeline.py::load": 2,
                    "src/pipeline.py::save": 1,
                    "src/pipeline.py::drop": 1,
                }
            ),
        )
    )

    assert profile is not None
    assert profile["fix_count"] == 5
    assert profile["window"] == "6 months"
    assert profile["last_fix_days_ago"] == 14
    assert profile["bug_magnet"] is True
    # Capped, path stripped (the caller already knows it), hedged.
    assert profile["top_symbols"] == {"run": 4, "load": 2, "save": 1}
    # The approximation caveat is in the tool docstring, not repeated per row:
    # a constant string once per target is pure per-file token cost.
    assert "symbols_note" not in profile


def test_bug_magnet_omitted_rather_than_reported_false():
    """Absent means "not a magnet": a false flag is not worth the tokens."""
    profile = _defect_profile(_meta(prior_defect_count=3))
    assert profile is not None
    assert "bug_magnet" not in profile


def test_naive_timestamps_are_treated_as_utc():
    """The column round-trips naive; comparing it to an aware now must not raise."""
    profile = _defect_profile(
        _meta(prior_defect_count=1, last_fix_at=datetime.now(UTC).replace(tzinfo=None))
    )
    assert profile is not None
    assert profile["last_fix_days_ago"] == 0


def test_top_fix_symbols_tolerates_junk():
    assert _top_fix_symbols(None) == {}
    assert _top_fix_symbols("not json") == {}
    assert _top_fix_symbols("[1, 2]") == {}


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        # The real counted history, decayed or plain, wins.
        (_meta(bug_magnet=True), "bug-prone"),
        (_meta(prior_defect_count=3), "bug-prone"),
        # Under the trigger, the file falls through to the next rule rather
        # than being called bug-prone on a keyword match.
        (_meta(prior_defect_count=2, churn_percentile=0.9), "churn-heavy"),
        # A pre-rollup index reports nothing here and classifies as before.
        (_meta(churn_percentile=0.9), "churn-heavy"),
    ],
)
def test_bug_prone_reads_counted_fixes_not_commit_subjects(meta, expected):
    assert _classify_risk_type(meta, dep_count=0, team_size=10) == expected


def test_fix_keywords_in_subjects_no_longer_classify():
    """The retired scan called a file bug-prone off "fix" in a commit subject.

    That matched doc and test commits, ignored recency entirely, and disagreed
    with the count the rest of the surfaces show. A file whose every commit says
    "fix" but which has no counted production fix is now simply stable.
    """
    meta = _meta(
        significant_commits_json=json.dumps(
            [{"message": "fix typo in README"}, {"message": "fix flaky test"}]
        )
    )
    assert _classify_risk_type(meta, dep_count=0, team_size=10) == "stable"


@pytest.mark.asyncio
async def test_get_risk_omits_the_block_without_fix_data(setup_mcp):
    """End to end: the fixture repo has no counted fixes, so no block appears."""
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/service.py"])
    assert "defect_profile" not in result["targets"]["src/auth/service.py"]


# ---------------------------------------------------------------------------
# The shared fix annotation, and the risk_summary clause built on it
# ---------------------------------------------------------------------------


def test_fix_annotation_is_silent_without_counted_fixes():
    assert fix_annotation(_meta()) is None


def test_fix_annotation_withholds_the_magnet_flag_without_an_age():
    # bug_magnet is a claim about RECENT fix pressure. With no timestamp the
    # same word would describe a file fixed four times last month and one
    # fixed four times two years ago, so the flag drops rather than mislead.
    out = fix_annotation(_meta(prior_defect_count=9, bug_magnet=True, last_fix_at=None))
    assert out == {"fix_count": 9}
    assert "bug_magnet" not in out


def test_fix_annotation_carries_count_age_and_flag():
    out = fix_annotation(
        _meta(
            prior_defect_count=5,
            bug_magnet=True,
            last_fix_at=datetime.now(UTC) - timedelta(days=14),
        )
    )
    assert out == {"fix_count": 5, "last_fix_days_ago": 14, "bug_magnet": True}


def test_defect_profile_still_builds_on_the_shared_annotation():
    # The profile is the annotation plus a window and symbols, so the recency
    # contract is enforced in exactly one place.
    profile = _defect_profile(
        _meta(
            prior_defect_count=2,
            bug_magnet=True,
            last_fix_at=datetime.now(UTC) - timedelta(days=3),
        )
    )
    assert profile["fix_count"] == 2
    assert profile["last_fix_days_ago"] == 3
    assert profile["bug_magnet"] is True
    assert profile["window"] == "6 months"


def test_risk_summary_clause_is_empty_without_fix_history():
    # Files that have never been fixed must not gain a dangling separator.
    assert _fix_clause(None) == ""
    assert _fix_clause({"fix_count": 4}) == ""  # count with no age


def test_risk_summary_clause_leads_with_fixes_and_closes_its_separator():
    clause = _fix_clause({"fix_count": 5, "last_fix_days_ago": 14, "bug_magnet": True})
    assert clause == "5 bug fixes in 6mo, last 14d ago (bug magnet), "


def test_risk_summary_clause_singularizes_one_fix():
    clause = _fix_clause({"fix_count": 1, "last_fix_days_ago": 2})
    assert clause == "1 bug fix in 6mo, last 2d ago, "
