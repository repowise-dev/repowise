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


def test_the_headline_states_the_changed_codes_health_on_both_sides():
    from repowise.core.analysis.change_health.file_scores import FileScoreDelta

    def row(path, base, head):
        return FileScoreDelta(path, base, head, 10 - base, 10 - head, 100, 100)

    delta = _delta([_finding(severity="critical")])
    delta.files = [row("app/a.py", 8.0, 7.0), row("app/b.py", 9.5, 9.5)]
    assert review_directive(delta).headline.endswith(
        "Health of the changed code: 8.75 to 8.25 (-0.50)."
    )
    delta.files = [row("app/b.py", 9.5, 9.5)]
    assert review_directive(delta).headline.endswith("Health of the changed code holds at 9.50.")


def test_a_move_names_the_structure_only_basis_instead_of_a_delta():
    from repowise.core.analysis.change_health.file_scores import FileScoreDelta

    delta = _delta([_finding(severity="critical")])
    delta.files = [FileScoreDelta("app/a.py", 5.51, 5.70, 4.49, 4.30, 100, 60)]
    delta.moved_total = 2
    headline = review_directive(delta).headline
    assert headline.endswith(
        "Code moved between files, so the changed code's health (structure only) is not compared."
    )
    assert "5.51" not in headline


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


def test_skipped_docs_beside_analysed_code_ask_for_nothing():
    delta = _delta()
    delta.skipped = {"CHANGES.rst": "not_code", "package.json": "not_code"}
    directive = review_directive(delta)
    assert directive.status == "clear_in_analyzed_scope"
    assert directive.reasons == ()
    assert all(a.kind != "review_skipped_files" for a in directive.actions)


def test_a_docs_only_change_says_there_is_no_code_rather_than_unknown():
    delta = _delta(status="unavailable", explanation="Only documentation changed")
    delta.skipped = {"README.md": "not_code"}
    directive = review_directive(delta)
    assert directive.status == "clear_in_analyzed_scope"
    assert "No code changed" in directive.headline
    assert "does not cover them" in directive.headline
    assert not directive.actions


def test_an_unsupported_code_file_still_leaves_the_verdict_unknown():
    delta = _delta(status="unavailable", explanation="nothing compared")
    delta.skipped = {"README.md": "not_code", "lib.ex": "not_health_analyzable"}
    directive = review_directive(delta)
    assert directive.status == "unknown"
    assert [a.targets for a in directive.actions] == [("lib.ex",)]


def test_a_query_only_change_is_clear_with_its_own_headline():
    delta = _delta(status="unavailable", explanation="Only query files changed")
    delta.skipped = {"queries/java.scm": "not_analyzable_data", "README.md": "not_code"}
    directive = review_directive(delta)
    assert directive.status == "clear_in_analyzed_scope"
    assert "query" in directive.headline
    assert "documentation or configuration" not in directive.headline
    assert not directive.actions


# ---------------------------------------------------------------------------
# Actions carry targets, not rendered strings
# ---------------------------------------------------------------------------


def test_each_finding_produces_an_inspect_action_with_its_id():
    delta = _delta([_finding(path="app/a.py"), _finding(path="app/b.py")])
    actions = [a for a in review_directive(delta).actions if a.kind == "inspect_finding"]
    assert len(actions) == 2
    assert all(a.targets and a.evidence_basis == "symbol_overlap" for a in actions)
    assert [a.priority for a in actions] == [0, 1]


def test_the_finding_the_headline_names_leads_the_reasons_and_actions():
    mediums = [_finding(severity="medium", path=f"app/m{i}.py") for i in range(3)]
    high = _finding(severity="high", path="app/h.py", biomarker="large_method")
    directive = review_directive(_delta([*mediums, high]))
    assert directive.headline == (
        "1 of 4 new findings needs review, starting with large_method in app/h.py."
    )
    assert directive.reasons[0].startswith("high defect: large_method")
    assert directive.actions[0].targets == (high.change_finding_id,)
    # The rest keep the order the delta gave them.
    assert [a.targets[0] for a in directive.actions[1:4]] == [
        f.change_finding_id for f in mediums
    ]


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

