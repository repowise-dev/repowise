"""The verdict is analysis, so it lives in core and carries no rendering.

These tests pin two things: the policy itself, and the boundary -- core emits
targets and states, never Markdown, URLs, tool-call syntax, or surface caps.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.change_health.models import ChangeFinding, ChangeHealthDelta
from repowise.core.analysis.review_directive import (
    CoveringTestEvidence,
    review_directive,
)


def _finding(
    *,
    severity: str = "high",
    dimension: str = "defect",
    path: str = "app/a.py",
    biomarker: str = "deep_nesting",
    line_start: int | None = 12,
    symbol: str | None = None,
) -> ChangeFinding:
    return ChangeFinding(
        change_finding_id=f"cf_{path}_{biomarker}_{severity}",
        change_kind="introduced",
        dimension=dimension,
        biomarker_type=biomarker,
        severity=severity,
        path=path,
        symbol=symbol,
        line_start=line_start,
        line_end=line_start,
        reason="",
        attribution_basis="symbol_overlap",
        attribution_confidence="high",
        attribution_detail="",
        suggestion="",
        follow_up="",
    )


def _delta(findings=(), *, status="available", skipped=(), explanation="") -> ChangeHealthDelta:
    return ChangeHealthDelta(
        status=status,
        explanation=explanation,
        base=None,
        head=None,
        comparison_basis="commit",
        fingerprint=None,
        findings=list(findings),
        skipped={path: "unsupported_language" for path in skipped},
    )


# ---------------------------------------------------------------------------
# Verdict policy
# ---------------------------------------------------------------------------


def test_blocking_finding_requires_review():
    directive = review_directive(_delta([_finding(severity="critical")]))
    assert directive.status == "review_required"
    assert "needs review" in directive.headline
    assert directive.evidence_state == "available"


def test_low_severity_only_recommends_review():
    directive = review_directive(_delta([_finding(severity="low")]))
    assert directive.status == "review_recommended"


def test_a_performance_finding_is_advisory_not_a_gate():
    directive = review_directive(_delta([_finding(severity="high", dimension="performance")]))
    assert directive.status == "review_recommended"


def test_nothing_found_is_clear_only_within_the_analyzed_scope():
    directive = review_directive(_delta())
    assert directive.status == "clear_in_analyzed_scope"
    assert "analyzed scope" in directive.headline


@pytest.mark.parametrize("status", ["unavailable", "unsupported_range", "too_large", "timeout"])
def test_a_comparison_that_did_not_happen_is_unknown(status):
    directive = review_directive(_delta(status=status, explanation="the repo is enormous"))
    assert directive.status == "unknown"
    assert directive.evidence_state == "unavailable"


@pytest.mark.parametrize("status", ["analyzer_mismatch", "rules_mismatch", "stale_baseline"])
def test_an_untrustworthy_baseline_is_degraded_not_merely_absent(status):
    directive = review_directive(_delta(status=status, explanation="analyzer moved"))
    assert directive.status == "unknown"
    assert directive.evidence_state == "degraded"


def test_a_clean_partial_comparison_is_not_reported_as_clean():
    directive = review_directive(_delta(status="partial"))
    assert directive.status == "unknown"
    assert directive.evidence_state == "partial"
    assert "not analysed" in directive.headline


def test_skipped_files_are_a_reason_not_a_clean_bill():
    directive = review_directive(_delta(skipped=["app/b.py", "app/c.py"]))
    assert any("not a clean bill" in reason for reason in directive.reasons)


# ---------------------------------------------------------------------------
# Actions carry targets, not rendered strings
# ---------------------------------------------------------------------------


def test_each_finding_produces_an_inspect_action_with_its_id():
    delta = _delta([_finding(path="app/a.py"), _finding(path="app/b.py")])
    actions = [a for a in review_directive(delta).actions if a.kind == "inspect_finding"]
    assert len(actions) == 2
    assert all(a.targets and a.evidence_basis == "symbol_overlap" for a in actions)
    assert [a.priority for a in actions] == [0, 1]


def test_measured_tests_become_a_run_action_with_every_target():
    tests = CoveringTestEvidence(state="available", tests_to_run=tuple(f"t{i}" for i in range(9)), basis="measured")
    action = next(a for a in review_directive(_delta(), tests).actions if a.kind == "run_tests")
    # Uncapped: a surface decides how many of these to show.
    assert len(action.targets) == 9
    assert action.evidence_basis == "measured"


def test_looking_and_finding_no_map_asks_for_coverage():
    directive = review_directive(_delta(), CoveringTestEvidence(state="unavailable"))
    kinds = {a.kind for a in directive.actions}
    assert "establish_test_coverage" in kinds
    assert "run_tests" not in kinds


def test_a_lane_nobody_consulted_asks_for_nothing():
    """Not consulting the test lane is not evidence that coverage is missing."""
    assert review_directive(_delta()).actions == ()
    assert review_directive(_delta(), CoveringTestEvidence()).actions == ()
    assert review_directive(_delta(), CoveringTestEvidence(state="unsupported")).actions == ()


def test_known_empty_test_evidence_does_not_ask_for_coverage():
    """The map exists and nothing covers these files. That is an answer."""
    directive = review_directive(_delta(), CoveringTestEvidence(state="available", tests_to_run=()))
    assert {a.kind for a in directive.actions} == set()


def test_skipped_files_become_an_action_naming_them():
    delta = _delta(skipped=["app/z.py", "app/b.py"])
    action = next(a for a in review_directive(delta).actions if a.kind == "review_skipped_files")
    assert action.targets == ("app/b.py", "app/z.py")


def test_core_emits_no_markdown_urls_or_call_syntax():
    delta = _delta([_finding()], skipped=["app/b.py"])
    tests = CoveringTestEvidence(state="available", tests_to_run=("tests/test_a.py::test_x",))
    directive = review_directive(delta, tests)

    prose = [directive.headline, *directive.reasons, *(a.explanation for a in directive.actions)]
    for text in prose:
        assert "`" not in text
        assert "**" not in text
        assert "http" not in text
        assert "(" not in text or ")" in text  # no dangling call syntax
        assert "get_change_risk" not in text
        assert "](" not in text


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------


def test_action_fingerprints_are_stable_across_runs():
    delta = _delta([_finding()])
    first = review_directive(delta).actions[0].fingerprint
    second = review_directive(delta).actions[0].fingerprint
    assert first == second


def test_fingerprints_separate_different_actions():
    delta = _delta([_finding(path="app/a.py"), _finding(path="app/b.py")])
    actions = review_directive(delta).actions
    assert len({a.fingerprint for a in actions}) == len(actions)


def test_directive_fingerprint_tracks_its_actions():
    clean = review_directive(_delta())
    flagged = review_directive(_delta([_finding()]))
    assert clean.fingerprint != flagged.fingerprint

