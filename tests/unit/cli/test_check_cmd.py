"""``repowise check``: policies.yaml over a change, and the gates without one."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

from repowise.cli.commands.check_cmd import check_command
from repowise.cli.commands.security_cmd import security_command
from tests.unit.analysis.test_security_gate_extras import KEY, _git, _write, init_repo
from tests.unit.cli.test_format_json_rollout import _split_runner

CATALOG = """\
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: payments
  annotations:
    repowise.dev/paths: src/pay/**
    repowise.dev/criticality: critical
    repowise.dev/tags: pci
spec: {type: service, lifecycle: production, owner: group:default/payments-team}
"""

AGENT_PCI = """\
rules:
  - name: agent-pci
    match:
      components: {tags: [pci]}
    when: {attribution: agent}
    action: fail
"""


@pytest.fixture
def repo(tmp_path):
    root = init_repo(tmp_path)
    (root / ".repowise").mkdir()
    (root / "src" / "pay").mkdir(parents=True)
    return root


def _commit(repo, name: str, text: str, message: str = "change") -> None:
    _write(repo, name, text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


def _policy(repo, text: str) -> None:
    (repo / ".repowise" / "policies.yaml").write_text(text, encoding="utf-8")


def _check(repo, *args: str):
    return _split_runner().invoke(check_command, ["main...HEAD", "--path", str(repo), *args])


def _agent_change(repo) -> None:
    _write(repo, ".repowise/components.yaml", CATALOG)
    _commit(repo, "src/pay/charge.py", "x = 1\n", "add charge\n\nGenerated with Claude Code")


def test_no_policy_file_has_nothing_to_evaluate(repo):
    _commit(repo, "app.py", "x = 2\n")
    table = _check(repo)
    assert table.exit_code == 0, table.output
    assert "nothing to evaluate" in table.output

    doc = json.loads(_check(repo, "--format", "json").stdout)
    assert doc["results"] == [] and doc["policy_file"] is None
    sarif = json.loads(_check(repo, "--format", "sarif").stdout)
    assert sarif["version"] == "2.1.0" and sarif["runs"][0]["results"] == []


def test_existing_gates_are_unchanged_with_or_without_a_policy(repo):
    _commit(repo, "cfg.py", f"AWS = '{KEY}'\n")
    args = ["check", "main...HEAD", "--path", str(repo), "--format", "json"]
    before = _split_runner().invoke(security_command, args)
    _policy(repo, "rules: [{name: sec, gate: security, action: warn}]")
    after = _split_runner().invoke(security_command, args)
    assert before.exit_code == after.exit_code == 1
    assert before.stdout == after.stdout


def test_agent_change_to_a_pci_component_fails(repo, tmp_path):
    _agent_change(repo)
    _policy(repo, AGENT_PCI)
    sarif_file, junit_file = tmp_path / "out.sarif", tmp_path / "out.xml"
    result = _check(
        repo, "--format", "json", "--sarif-file", str(sarif_file), "--junit-file", str(junit_file)
    )
    assert result.exit_code == 1, result.output
    doc = json.loads(result.stdout)
    (row,) = doc["results"]
    assert row["outcome"] == "triggered" and row["components"] == ["payments"]
    assert row["locations"] == [{"path": "src/pay/charge.py", "line": None}]
    kinds = {i["kind"]: i for i in doc["evidence"]["items"]}
    assert kinds["provenance"]["value"]["label"] == "agent"
    assert kinds["reaching_tests"]["status"] == "unknown"  # no index

    sarif = json.loads(sarif_file.read_text(encoding="utf-8"))
    assert sarif["runs"][0]["results"][0]["level"] == "error"
    suite = ET.fromstring(junit_file.read_text(encoding="utf-8")).find("testsuite")
    assert suite.get("failures") == "1"


def test_an_unattributed_change_passes_and_missing_evidence_never_fails(repo):
    _write(repo, ".repowise/components.yaml", CATALOG)
    _commit(repo, "src/pay/charge.py", "x = 1\n", "add charge")
    _policy(
        repo,
        AGENT_PCI
        + """\
  - name: untested-pci
    match: {components: {tags: [pci]}}
    when: {reaching_tests: missing}
    action: fail
""",
    )
    result = _check(repo)
    assert result.exit_code == 0, result.output
    assert "agent-pci" in result.output and "not evaluated" in result.output.lower()


def test_security_gate_wrapped_as_a_rule(repo):
    _policy(repo, "rules: [{name: no-secrets, gate: security, with: {fail_on: high}}]")
    _commit(repo, "app.py", "x = 2\n")
    clean = _check(repo, "--format", "junit")
    assert clean.exit_code == 0, clean.output
    assert ET.fromstring(clean.stdout).find("testsuite").get("failures") == "0"

    _commit(repo, "cfg.py", f"AWS = '{KEY}'\n")
    dirty = _check(repo, "--format", "json")
    assert dirty.exit_code == 1, dirty.output
    (row,) = json.loads(dirty.stdout)["results"]
    assert row["outcome"] == "triggered" and row["locations"][0]["path"] == "cfg.py"
    assert "repowise security check --fail-on=high main...HEAD" in row["message"]
    assert row["locations"][0]["fingerprint"]


@pytest.mark.parametrize(
    ("policy", "problem"),
    [
        ("rules: [{name: x, when: {risk_above: 5}, action: fail}]", "risk is not calibrated"),
        ("rules: [{name: x, gate: security, with: {fail_under: 80}}]", "has no option 'fail_under'"),
        ("rules: [{name: x, gate: security, with: {format: sarif}}]", "has no option 'format'"),
    ],
)
def test_invalid_policy_exits_two(repo, policy, problem):
    _commit(repo, "app.py", "x = 2\n")
    _policy(repo, policy)
    result = _check(repo, "--format", "json")
    assert result.exit_code == 2
    doc = json.loads(result.stdout)
    assert doc["error"] == "policy_invalid" and problem in doc["message"]


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [
        (1, "Traceback (most recent call last):\n  ...\nRuntimeError: boom\n"),
        (1, '{"gate": {"passed": true}}'),
        (0, '{"gate": {"passed": false}}'),
        (1, ""),
    ],
)
def test_a_gate_without_a_matching_verdict_is_not_evaluated(repo, monkeypatch, returncode, stdout):
    import subprocess

    from repowise.cli.commands import check_cmd

    real = subprocess.run

    def crashed(argv, **kwargs):
        if "repowise.cli.main" not in argv:
            return real(argv, **kwargs)
        return subprocess.CompletedProcess(argv, returncode, stdout, "RuntimeError: boom\n")

    monkeypatch.setattr(check_cmd.subprocess, "run", crashed)
    _policy(repo, "rules: [{name: sec, gate: security}]")
    _commit(repo, "app.py", "x = 2\n")
    result = _check(repo, "--format", "json")
    assert result.exit_code == 2, result.output
    (row,) = json.loads(result.stdout)["results"]
    assert row["outcome"] == "unknown"


def test_a_gate_that_runs_too_long_is_not_evaluated(repo, monkeypatch):
    import subprocess

    from repowise.cli.commands import check_cmd

    real = subprocess.run

    def slow(argv, **kwargs):
        if "repowise.cli.main" not in argv:
            return real(argv, **kwargs)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(check_cmd.subprocess, "run", slow)
    _policy(repo, "rules: [{name: sec, gate: security, action: warn}]")
    _commit(repo, "app.py", "x = 2\n")
    result = _check(repo, "--format", "json")
    assert result.exit_code == 0
    (row,) = json.loads(result.stdout)["results"]
    assert row["outcome"] == "unknown" and "did not finish" in row["message"]


def test_gate_arguments_scope_doc_drift_and_end_options_before_revspec():
    from repowise.cli.commands.check_cmd import _gate_argv
    from repowise.core.policy import parse_policy

    sec, drift, pinned = parse_policy(
        """\
rules:
  - {name: s, gate: security, with: {baseline: -odd.json}}
  - {name: d, gate: doc_drift}
  - {name: p, gate: doc_drift, with: {since: auto}}
"""
    ).rules
    assert _gate_argv(sec, "-x...HEAD") == [
        "security", "check", "--baseline=-odd.json", "--format", "json", "--", "-x...HEAD"
    ]
    assert _gate_argv(drift, "main...HEAD") == [
        "doc-drift", "--check", "--since=main...HEAD", "--format", "json"
    ]
    assert "--since=auto" in _gate_argv(pinned, "main...HEAD")
