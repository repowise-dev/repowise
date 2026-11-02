"""The health gate's verdict over a comparison, and its renderings."""

from __future__ import annotations

import json

import pytest

from repowise.core.analysis.change_health.models import (
    ChangeFinding,
    ChangeHealthDelta,
    ScopeCounts,
)
from repowise.core.analysis.health_gate import (
    ERROR,
    NOTE,
    WARNING,
    GateFinding,
    NotEvaluatedError,
    as_json,
    assemble,
    evaluate,
    gate_summary,
    github_annotations,
    render_gitlab,
    render_markdown,
    render_sarif,
    reproduce_command,
)


def finding(**kw) -> ChangeFinding:
    base = dict(
        change_finding_id="chf_1",
        change_kind="introduced",
        dimension="defect",
        biomarker_type="complex_method",
        severity="high",
        path="app.py",
        symbol="tangle",
        line_start=5,
        line_end=40,
        reason="tangle has cyclomatic complexity 26",
        attribution_basis="added_lines",
        attribution_confidence="high",
        attribution_detail="",
        suggestion="",
        follow_up="",
        finding_id="finding_abc",
    )
    base.update(kw)
    return ChangeFinding(**base)


def delta(*findings: ChangeFinding, status="available", **kw) -> ChangeHealthDelta:
    return ChangeHealthDelta(
        status=status,
        explanation=kw.pop("explanation", "Compared."),
        base=None,
        head=None,
        comparison_basis=kw.pop("comparison_basis", "both_sides_analyzed"),
        fingerprint=None,
        scope=kw.pop("scope", ScopeCounts(changed=1, eligible=1, analyzed=1)),
        findings=list(findings),
        **kw,
    )


def levels(result) -> list[str]:
    return [f.level for f in result.findings]


# -- verdict -----------------------------------------------------------------


def test_an_introduced_finding_the_change_wrote_fails():
    result = evaluate(delta(finding()))
    assert result.verdict == "fail" and not result.passed
    assert levels(result) == [ERROR]
    assert result.findings[0].fingerprint == "finding_abc"


def test_a_worsened_finding_warns_by_default_and_fails_on_worsened():
    worse = finding(change_kind="worsened", severity="critical", severity_before="high")
    assert evaluate(delta(worse)).verdict == "warn"
    assert evaluate(delta(worse), fail_on="worsened").verdict == "fail"
    assert "worsened from high to critical" in evaluate(delta(worse)).findings[0].message


@pytest.mark.parametrize("basis", ["moved", "file_change", "context_change", "unknown"])
def test_a_finding_the_change_did_not_write_is_context(basis):
    result = evaluate(delta(finding(attribution_basis=basis)), fail_on="worsened")
    assert result.verdict == "pass" and levels(result) == [NOTE]


@pytest.mark.parametrize("marker", ["churn_risk", "change_entropy", "coverage_gap", "io_in_loop"])
def test_history_coverage_and_performance_markers_never_decide(marker):
    result = evaluate(delta(finding(biomarker_type=marker)), fail_on="worsened")
    assert result.verdict == "pass" and levels(result) == [NOTE]


def test_a_test_file_finding_warns_at_most():
    result = evaluate(delta(finding(path="tests/test_app.py")))
    assert result.verdict == "warn" and levels(result) == [WARNING]


@pytest.mark.parametrize("severity", ["low", "medium"])
def test_a_finding_below_high_severity_warns_at_most(severity):
    result = evaluate(delta(finding(severity=severity)), fail_on="worsened")
    assert result.verdict == "warn" and levels(result) == [WARNING]


def test_nothing_readable_is_not_evaluated():
    d = delta(
        status="unavailable",
        scope=ScopeCounts(changed=1, eligible=1),
        skipped={"huge.py": "too_large"},
    )
    with pytest.raises(NotEvaluatedError):
        evaluate(d)


def test_only_generated_code_passes_with_a_note():
    d = delta(
        status="unavailable",
        scope=ScopeCounts(changed=1, eligible=1, skipped=1),
        skipped={"gen.py": "generated"},
    )
    assert evaluate(d).verdict == "pass"


def test_a_file_that_failed_to_parse_warns():
    d = delta(status="partial", skipped={"bad.py": "parse_failed", "old.py": "base_unreadable"})
    result = evaluate(d)
    assert result.verdict == "warn"
    assert [f.path for f in result.findings] == ["bad.py", "old.py"]
    assert result.notes and "not analysed" in result.notes[0]


def test_a_skipped_unsupported_file_is_a_note_not_a_warning():
    d = delta(status="partial", skipped={"run.sh": "unsupported_language"})
    result = evaluate(d)
    assert result.verdict == "pass" and "run.sh (unsupported language)" in result.notes[0]


def test_nothing_to_compare_passes_with_a_note():
    d = delta(
        status="unavailable",
        explanation="Only documentation or configuration files changed.",
        scope=ScopeCounts(changed=1),
    )
    result = evaluate(d)
    assert result.verdict == "pass"
    assert result.notes == ("Only documentation or configuration files changed.",)


@pytest.mark.parametrize(
    "status, kw",
    [
        ("too_large", {"comparison_basis": "not_compared"}),
        ("unsupported_range", {"comparison_basis": "not_compared"}),
        ("stale_baseline", {}),
        ("unavailable", {"comparison_basis": "not_compared"}),
        (
            "unavailable",
            {
                "scope": ScopeCounts(changed=2, eligible=2, failed=2),
                "skipped": {"a.py": "parse_failed", "b.py": "base_parse_failed"},
            },
        ),
    ],
)
def test_a_comparison_that_judged_nothing_is_not_evaluated(status, kw):
    with pytest.raises(NotEvaluatedError) as caught:
        evaluate(delta(status=status, **kw))
    assert caught.value.code in (status, "not_analysed")


def test_findings_from_another_source_join_the_verdict():
    extra = GateFinding("other", "rule", ERROR, "x.py", 3, "m", "fp")
    result = evaluate(delta(), extra=[extra])
    assert result.verdict == "fail" and result.findings == (extra,)


def test_assemble_orders_errors_first():
    a = GateFinding("s", "r", NOTE, "a.py", 1, "m", "1")
    b = GateFinding("s", "r", ERROR, "b.py", 1, "m", "2")
    c = GateFinding("s", "r", WARNING, "c.py", 1, "m", "3")
    assert levels(assemble([a, b, c], fail_on="introduced")) == [ERROR, WARNING, NOTE]


# -- renderings --------------------------------------------------------------


def mixed():
    return evaluate(
        delta(
            finding(),
            finding(change_kind="worsened", symbol="knot", finding_id="finding_def"),
            finding(attribution_basis="moved", symbol="carried", finding_id="finding_ghi"),
        )
    )


def test_json_carries_the_gate_shape_policy_rules_read():
    d = delta(finding())
    doc = as_json(evaluate(d), d, label="main...HEAD")
    json.dumps(doc)
    from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION

    assert doc["analyzer_version"] == HEALTH_ANALYZER_VERSION
    gate = doc["gate"]
    assert gate["passed"] is False and gate["verdict"] == "fail"
    (row,) = gate["failing"]
    assert row["file_path"] == "app.py" and row["line_number"] == 5
    assert row["fingerprint"] == "finding_abc"


def test_github_annotates_errors_and_warnings_only():
    lines = github_annotations(mixed())
    assert [line.split(" ", 1)[0] for line in lines] == ["::error", "::warning"]
    assert lines[0].startswith("::error file=app.py,line=5,")


def test_a_failing_annotation_carries_the_first_sentence_of_its_fix():
    error, warning = github_annotations(mixed())
    assert error.endswith(
        "::tangle has cyclomatic complexity 26 (introduced, high). Reduce cyclomatic complexity."
    )
    assert "Reduce cyclomatic complexity" not in warning


def test_a_failing_run_ends_in_one_reproduce_notice():
    result = mixed()
    command = reproduce_command("origin/main...HEAD", result, ("error",))
    assert command == (
        "repowise health check origin/main...HEAD --fail-on introduced --fail-at high "
        "--diff-risk error"
    )
    lines = github_annotations(result, reproduce=command)
    assert lines[-1] == f"::notice::Reproduce locally: pip install repowise, then {command}"
    assert sum(line.startswith("::notice") for line in lines) == 1
    assert "Reproduce locally" in gate_summary(result, reproduce=command).headline
    text = render_markdown(result, label="x", scope="s", reproduce=command)
    assert f"Reproduce locally: `pip install repowise`, then `{command}`" in text


def test_a_passing_run_carries_no_hint_or_reproduce_line():
    result = evaluate(delta(finding(change_kind="worsened")))
    assert result.passed
    command = reproduce_command("main...HEAD", result)
    lines = github_annotations(result, reproduce=command)
    assert [line.split(" ", 1)[0] for line in lines] == ["::warning"]
    assert lines[0].endswith("(worsened, high)")
    assert "Reproduce" not in render_markdown(result, label="x", scope="s", reproduce=command)
    assert "Reproduce" not in gate_summary(result, reproduce=command).headline


def test_a_diff_risk_error_keeps_its_own_message():
    risk = GateFinding("diff-risk", "added-suppression", ERROR, "a.py", 1, "Adds a noqa.", "f")
    (line,) = github_annotations(assemble([risk], fail_on="introduced"))
    assert line.endswith("::Adds a noqa.")


def test_sarif_levels_follow_the_gate_with_line_free_fingerprints():
    log = render_sarif(mixed(), tool_version="0")
    results = log["runs"][0]["results"]
    assert [r["level"] for r in results] == ["error", "warning"]
    assert results[0]["partialFingerprints"] == {"repowiseHealth/v1": "finding_abc"}
    assert log["runs"][0]["properties"] == {"verdict": "fail"}


def test_gitlab_issues_are_major_and_minor():
    issues = render_gitlab(mixed())
    assert [i["severity"] for i in issues] == ["major", "minor"]
    assert issues[0]["check_name"] == "repowise-health/complex_method"


def test_markdown_leads_with_the_verdict_and_folds_the_context():
    text = render_markdown(mixed(), label="main...HEAD", scope="1 of 1 changed file")
    assert text.startswith("**Health: 1 finding fails the gate**")
    assert "<details>" in text and "<summary>Context (moved" in text
    assert "Git history, coverage and file scores never decide" in text


@pytest.mark.parametrize(
    "fail_at, severity, level",
    [
        ("medium", "medium", ERROR),
        ("high", "medium", WARNING),
        ("high", "high", ERROR),
        ("critical", "high", WARNING),
        ("critical", "critical", ERROR),
    ],
)
def test_fail_at_is_the_lowest_severity_that_fails(fail_at, severity, level):
    assert levels(evaluate(delta(finding(severity=severity)), fail_at=fail_at)) == [level]


def test_an_unknown_option_is_refused():
    with pytest.raises(ValueError):
        evaluate(delta(), fail_at="low")
    with pytest.raises(ValueError):
        evaluate(delta(), fail_on="resolved")


def test_health_rules_parse_from_any_revision_and_refuse_a_non_object():
    from repowise.core.analysis.health.config import HealthConfig

    assert HealthConfig.from_json('{"disabled_biomarkers": ["complex_method"]}').disabled_biomarkers
    with pytest.raises(ValueError):
        HealthConfig.from_json("[1, 2]")
    with pytest.raises(ValueError):
        HealthConfig.from_json("{oops")
