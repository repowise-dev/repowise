"""``repowise.core.policy.rules``: reading policies.yaml and evaluating it over evidence."""

from __future__ import annotations

import pytest

from repowise.core.policy import (
    GateRun,
    PolicyError,
    evaluate,
    exit_code,
    load_policy,
    parse_policy,
)

PCI_RULE = """\
rules:
  - name: agent-pci-needs-owner
    match:
      components: {tags: [pci]}
    when:
      attribution: agent
      reaching_tests: missing
    action: require_owner_review
"""


def _item(kind, details, value=None, status="ok", basis="measured"):
    return {
        "kind": kind,
        "label": kind,
        "status": status,
        "basis": basis,
        "value": value,
        "details": details,
        "source": "test",
    }


def _evidence(*items):
    return {
        "schema_version": 1,
        "change_id": "abc",
        "revspec": "main...HEAD",
        "generated_at": "2026-10-14T00:00:00+00:00",
        "items": list(items),
    }


PAYMENTS = {
    "name": "payments",
    "criticality": "critical",
    "tags": ["pci"],
    "owner": "payments-team",
    "files": ["src/pay/charge.py", "src/pay/refund.py"],
    "bus_factor": 1,
}
DOCS = {"name": "docs", "criticality": "low", "tags": [], "owner": None, "files": ["docs/a.md"]}


def _components(*rows):
    return _item("components_touched", list(rows), value=len(rows), basis="declared")


def _provenance(label, tiers=()):
    rows = [{"sha": f"s{i}", "label": "agent", "tier": t} for i, t in enumerate(tiers)]
    return _item("provenance", rows, value={"label": label}, basis="declared")


def _tests(**by_path):
    return _item("reaching_tests", [{"path": p, "tests": t} for p, t in by_path.items()])


# -- parsing ------------------------------------------------------------------


def test_parses_a_condition_rule_and_a_gate_rule():
    policy = parse_policy(
        PCI_RULE
        + """\
  - name: no-new-secrets
    gate: security
    with: {fail_on: med}
"""
    )
    rule, gate = policy.rules
    assert rule.match.tags == ("pci",) and rule.when["attribution"] == ("agent",)
    assert gate.gate == "security" and gate.action == "fail" and gate.gate_args == {"fail_on": "med"}


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("rules: []", "'rules' must be a non-empty list"),
        ("version: 2\nrules: [{name: a, when: {decision_crossed: true}, action: warn}]", "version 2"),
        ("rules: [{name: A!, when: {decision_crossed: true}, action: warn}]", "'name' is required"),
        ("rules: [{name: a, when: {decision_crossed: true}, action: block}]", "'action' must be one of"),
        ("rules: [{name: a, when: {risk_above: 7}, action: fail}]", "risk is not calibrated"),
        ("rules: [{name: a, action: fail}]", "needs at least one condition"),
        ("rules: [{name: a, when: {attribution: robot}, action: fail}]", "attribution: must be"),
        ("rules: [{name: a, when: {reaching_tests: none}, action: fail}]", "'missing' or 'present'"),
        ("rules: [{name: a, when: {bus_factor_below: 0}, action: fail}]", "at least 1"),
        ("rules: [{name: a, gate: lint}]", "'gate' must be one of"),
        ("rules: [{name: a, gate: security, when: {decision_crossed: true}}]", "does not go with 'gate'"),
        ("rules: [{name: a, when: {decision_crossed: true}, action: warn, with: {x: 1}}]", "only goes with"),
        (
            "rules: [{name: a, match: {components: {team: x}}, when: {decision_crossed: true}, action: warn}]",
            "unknown key 'team'",
        ),
        ("rules: [{name: a, gate: security}, {name: a, gate: coverage}]", "duplicate rule name 'a'"),
        ("rules: [{name: a, gate: security, colour: red}]", "unknown key 'colour'"),
        ("rules: [", "invalid YAML"),
    ],
)
def test_invalid_policies_name_the_problem(text, problem):
    with pytest.raises(PolicyError) as err:
        parse_policy(text)
    assert any(problem in p for p in err.value.problems), err.value.problems


def test_every_problem_is_listed_at_once():
    with pytest.raises(PolicyError) as err:
        parse_policy("rules: [{name: a, action: block}, {name: b, gate: lint}]")
    assert len(err.value.problems) == 3


def test_load_policy_absent_is_none_and_named_missing_file_is_an_error(tmp_path):
    assert load_policy(tmp_path) is None
    with pytest.raises(PolicyError):
        load_policy(tmp_path, "custom.yaml")
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "policies.yaml").write_text(PCI_RULE, encoding="utf-8")
    assert load_policy(tmp_path).source == ".repowise/policies.yaml"


# -- evaluating ---------------------------------------------------------------


def test_agent_change_to_untested_pci_component_requires_owner_review():
    policy = parse_policy(PCI_RULE)
    evidence = _evidence(
        _components(PAYMENTS, DOCS),
        _provenance("agent", tiers=(2,)),
        _tests(**{"src/pay/charge.py": [], "src/pay/refund.py": ["tests/test_refund.py"], "x.py": []}),
    )
    (result,) = evaluate(policy, evidence)
    assert result.outcome == "triggered"
    assert result.components == ["payments"] and result.owners == ["payments-team"]
    # Only the untested file inside the matched component is located.
    assert result.locations == [("src/pay/charge.py", None, None)]
    assert "payments-team" in result.message
    assert result.evidence == {
        "components_touched": "declared",
        "provenance": "declared",
        "reaching_tests": "measured",
    }
    assert not result.fails and exit_code([result]) == 0


def test_human_or_tested_changes_pass():
    policy = parse_policy(PCI_RULE)
    human = evaluate(policy, _evidence(_components(PAYMENTS), _provenance("human_attested")))
    assert human[0].outcome == "passed"
    tested = _tests(**{"src/pay/charge.py": ["t1"], "src/pay/refund.py": ["t2"]})
    covered = evaluate(policy, _evidence(_components(PAYMENTS), _provenance("agent"), tested))
    assert covered[0].outcome == "passed"


def test_no_matching_component_is_not_applicable():
    (result,) = evaluate(parse_policy(PCI_RULE), _evidence(_components(DOCS), _provenance("agent")))
    assert result.outcome == "not_applicable"


def test_exclude_removes_a_matched_component():
    policy = parse_policy(
        PCI_RULE.replace("    when:", "    exclude:\n      components: {names: [payments]}\n    when:")
    )
    (result,) = evaluate(policy, _evidence(_components(PAYMENTS), _provenance("agent")))
    assert result.outcome == "not_applicable"


def test_unknown_evidence_never_fires():
    policy = parse_policy(PCI_RULE)
    no_tests = _evidence(
        _components(PAYMENTS),
        _provenance("agent"),
        _item("reaching_tests", [], status="unknown", basis="unresolved"),
    )
    (result,) = evaluate(policy, no_tests)
    assert result.outcome == "unknown" and "reaching tests are not known" in result.message
    (missing,) = evaluate(policy, _evidence(_components(PAYMENTS)))
    assert missing.outcome == "unknown"
    (no_components,) = evaluate(policy, _evidence())
    assert no_components.outcome == "unknown"


def test_a_false_condition_wins_over_an_unknown_one():
    policy = parse_policy(PCI_RULE)
    (result,) = evaluate(policy, _evidence(_components(PAYMENTS), _provenance("human_attested")))
    assert result.outcome == "passed"


def test_whole_change_conditions_and_fail_exit_code():
    policy = parse_policy(
        """\
rules:
  - name: decisions
    when: {decision_crossed: true}
    action: fail
  - name: contracts
    when: {contract_break: true}
    action: warn
  - name: low-bus-factor
    match: {components: {criticality: [critical]}}
    when: {bus_factor_below: 2, agent_tier: [1]}
    action: require_tests
"""
    )
    evidence = _evidence(
        _components(PAYMENTS),
        _provenance("agent", tiers=(1, 3)),
        _item("decisions_crossed", [{"id": "d1", "title": "Use Stripe", "paths": ["src/pay/charge.py"]}]),
        _item("contracts", [{"path": "src/pay/charge.py", "breaking": False}]),
    )
    decisions, contracts, bus = evaluate(policy, evidence)
    assert decisions.outcome == "triggered" and decisions.fails
    assert "Use Stripe" in decisions.message
    assert decisions.locations == [("src/pay/charge.py", None, None)]
    assert contracts.outcome == "passed"
    assert bus.outcome == "triggered" and "payments (1)" in bus.message
    assert exit_code([decisions, contracts, bus]) == 1


def test_bus_factor_unknown_for_a_component_is_unknown():
    policy = parse_policy("rules: [{name: b, when: {bus_factor_below: 2}, action: warn}]")
    (result,) = evaluate(policy, _evidence(_components(PAYMENTS, DOCS)))
    assert result.outcome == "unknown"


def test_gate_runs_map_to_outcomes_and_exit_codes():
    policy = parse_policy(
        "rules: [{name: sec, gate: security}, {name: cov, gate: coverage, action: warn}]"
    )
    failed = evaluate(policy, None, {"sec": GateRun(1, "x", (("a.py", 3, "fp1"),)), "cov": GateRun(1, "y")})
    assert [r.outcome for r in failed] == ["triggered", "triggered"]
    assert failed[0].locations == [("a.py", 3, "fp1")] and exit_code(failed) == 1

    broken = evaluate(policy, None, {"sec": GateRun(2, "no report"), "cov": GateRun(0, "")})
    assert [r.outcome for r in broken] == ["unknown", "passed"] and exit_code(broken) == 2

    warn_only = evaluate(policy, None, {"sec": GateRun(0, ""), "cov": GateRun(2, "no report")})
    assert exit_code(warn_only) == 0


def test_a_file_nothing_knows_a_test_for_is_not_missing():
    policy = parse_policy(PCI_RULE)
    unknown = _tests(**{"src/pay/charge.py": None, "src/pay/refund.py": ["t2"]})
    (result,) = evaluate(policy, _evidence(_components(PAYMENTS), _provenance("agent"), unknown))
    assert result.outcome == "unknown"
    # A measured absence elsewhere in scope still fires.
    mixed = _tests(**{"src/pay/charge.py": None, "src/pay/refund.py": []})
    (fired,) = evaluate(policy, _evidence(_components(PAYMENTS), _provenance("agent"), mixed))
    assert fired.outcome == "triggered" and fired.locations == [("src/pay/refund.py", None, None)]


def test_a_partly_known_tests_item_still_answers_for_the_files_in_scope():
    """The checklist reads ``unknown`` when one changed file is not known; rows still decide."""
    policy = parse_policy(
        "rules: [{name: t, match: {components: {names: [payments]}}, when: {reaching_tests: present}, action: warn}]"
    )
    rows = [
        {"path": "src/pay/charge.py", "tests": ["t1"]},
        {"path": "src/pay/refund.py", "tests": ["t2"]},
        {"path": "docs/a.md", "tests": None},
    ]
    partial = _item("reaching_tests", rows, status="unknown", basis="inferred")
    (result,) = evaluate(policy, _evidence(_components(PAYMENTS), partial))
    assert result.outcome == "triggered"


def test_no_changed_source_file_in_scope_is_not_missing():
    policy = parse_policy("rules: [{name: t, when: {reaching_tests: missing}, action: fail}]")
    (result,) = evaluate(policy, _evidence(_tests()))
    assert result.outcome == "passed"
