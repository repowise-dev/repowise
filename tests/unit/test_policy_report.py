"""``repowise.core.policy.report``: SARIF 2.1.0 and JUnit XML for policy results."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from repowise.core.policy import GateRun, evaluate, parse_policy
from repowise.core.policy.report import render_junit, render_sarif

POLICY = parse_policy(
    """\
rules:
  - name: decisions
    description: Changes that cross a governing decision fail.
    when: {decision_crossed: true}
    action: fail
  - name: agent-review
    when: {attribution: agent}
    action: require_owner_review
  - name: tests
    when: {reaching_tests: missing}
    action: require_tests
  - name: sec
    gate: security
"""
)
EVIDENCE = {
    "schema_version": 1,
    "change_id": "abc",
    "revspec": "main...HEAD",
    "generated_at": "now",
    "items": [
        {
            "kind": "decisions_crossed",
            "status": "ok",
            "basis": "declared",
            "details": [{"id": "d1", "title": "T", "paths": ["src/a.py"]}],
        },
        {"kind": "provenance", "status": "ok", "basis": "declared", "value": "agent", "details": []},
        {"kind": "reaching_tests", "status": "unknown", "basis": "unresolved", "details": []},
    ],
}


def _results():
    return evaluate(POLICY, EVIDENCE, {"sec": GateRun(0, "")})


def test_sarif_has_the_fields_code_scanning_and_reviewdog_read():
    log = render_sarif(POLICY, _results(), tool_version="1.2.3", revspec="main...HEAD")
    assert log["version"] == "2.1.0" and log["$schema"].endswith("sarif-2.1.0.json")
    (run,) = log["runs"]
    driver = run["tool"]["driver"]
    assert driver["name"] == "repowise-check" and driver["version"] == "1.2.3"
    assert [r["id"] for r in driver["rules"]] == [
        "policy/decisions",
        "policy/agent-review",
        "policy/tests",
        "policy/sec",
    ]
    for rule in driver["rules"]:
        assert rule["shortDescription"]["text"] and rule["fullDescription"]["text"]
    decision, review = run["results"]
    assert decision["ruleId"] == "policy/decisions" and decision["level"] == "error"
    assert decision["ruleIndex"] == 0
    loc = decision["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"] == {"uri": "src/a.py", "uriBaseId": "%SRCROOT%"}
    assert decision["message"]["text"]
    assert decision["partialFingerprints"]["repowisePolicy/v1"]
    assert decision["properties"]["action"] == "fail"
    assert decision["properties"]["evidence"] == {"decisions_crossed": "declared"}
    # A change-level result with no file of its own sits on the policy file.
    assert review["level"] == "warning"
    assert review["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == (
        ".repowise/policies.yaml"
    )
    # Not evaluated is reported on the run, never as a result.
    assert run["properties"]["notEvaluated"] == ["tests"]


def test_fingerprints_are_stable_across_runs():
    one = render_sarif(POLICY, _results(), tool_version="1", revspec="a...b")
    two = render_sarif(POLICY, _results(), tool_version="2", revspec="c...d")
    prints = [[r["partialFingerprints"] for r in log["runs"][0]["results"]] for log in (one, two)]
    assert prints[0] == prints[1]


def test_junit_marks_fail_skipped_and_passing_cases():
    root = ET.fromstring(render_junit(_results(), revspec="main...HEAD"))
    (suite,) = root.findall("testsuite")
    assert suite.get("tests") == "4" and suite.get("failures") == "1" and suite.get("skipped") == "1"
    cases = {c.get("name"): c for c in suite.findall("testcase")}
    assert cases["decisions"].find("failure").get("type") == "fail"
    assert cases["decisions"].find("failure").text == "src/a.py"
    assert cases["tests"].find("skipped") is not None
    assert "require_owner_review" in cases["agent-review"].find("system-out").text
    assert list(cases["sec"]) == []


def test_empty_results_render_valid_documents():
    empty = parse_policy("rules: [{name: s, gate: security}]")
    log = render_sarif(empty, [], tool_version="1", revspec="")
    assert log["runs"][0]["results"] == []
    assert ET.fromstring(render_junit([], revspec="")).find("testsuite").get("tests") == "0"


def test_findings_in_one_file_keep_their_own_fingerprints():
    policy = parse_policy("rules: [{name: sec, gate: security}]")
    run = GateRun(1, "x", (("a.py", 3, "f1"), ("a.py", 9, "f2"), ("a.py", 12, None)))
    log = render_sarif(policy, evaluate(policy, None, {"sec": run}), tool_version="1", revspec="")
    prints = {r["partialFingerprints"]["repowisePolicy/v1"] for r in log["runs"][0]["results"]}
    assert len(prints) == 3


def test_a_policy_outside_the_repository_locates_on_a_repo_file():
    policy = parse_policy(
        "rules: [{name: d, when: {decision_crossed: true}, action: warn}]",
        "C:/elsewhere/policies.yaml",
    )
    evidence = {
        "items": [
            {"kind": "decisions_crossed", "status": "ok", "basis": "declared",
             "details": [{"id": "d1", "title": "T"}]},
        ]
    }
    log = render_sarif(
        policy, evaluate(policy, evidence), tool_version="1", revspec="", fallback_path="src/a.py"
    )
    (res,) = log["runs"][0]["results"]
    assert res["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "src/a.py"
