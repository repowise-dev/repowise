"""``repowise health check``: the index-free health gate on a real git repository."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from repowise.cli.commands.health_cmd import health_command
from tests.unit.change_health.conftest import Repo, git, python_complex
from tests.unit.cli.test_format_json_rollout import _split_runner

SIMPLE = "def ok():\n    return 1\n"


@pytest.fixture
def repo(tmp_path) -> Repo:
    r = Repo(tmp_path / "repo")
    r.commit("base", {"app.py": SIMPLE, "knot.py": python_complex("knot", 25)})
    git(r.path, "checkout", "-q", "-b", "feature")
    return r


def check(repo: Repo, *args: str, env=None):
    return _split_runner().invoke(
        health_command,
        ["check", "main...HEAD", "--path", str(repo.path), *args],
        env=env or {},
    )


def test_a_new_complex_function_fails(repo):
    repo.commit("add", {"app.py": SIMPLE + "\n\n" + python_complex("tangle", 25)})
    result = check(repo, "--format", "json")
    assert result.exit_code == 1, result.output
    doc = json.loads(result.stdout)
    assert doc["gate"]["verdict"] == "fail"
    (row,) = doc["gate"]["failing"]
    assert (row["file_path"], row["symbol"], row["change_kind"]) == ("app.py", "tangle", "introduced")


def test_moving_and_renaming_a_complex_function_passes(repo):
    repo.move("knot.py", "pkg/knot.py")
    repo.commit("rename")
    repo.commit("split", {"pkg/knot.py": SIMPLE, "pkg/moved.py": python_complex("knot", 25)})
    result = check(repo, "--format", "json", "--fail-on", "worsened")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["gate"]["failing"] == []


def test_history_only_churn_passes(repo):
    for i in range(6):
        repo.commit(f"touch {i}", {"knot.py": f"# rev {i}\n" + python_complex("knot", 25)})
    result = check(repo, "--fail-on", "worsened")
    assert result.exit_code == 0, result.output
    assert "Gate passed" in result.output


def test_a_docs_only_change_passes_with_a_note(repo):
    repo.commit("docs", {"README.md": "# hi\n"})
    result = check(repo, "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["gate"]["notes"]


def test_github_format_annotates_and_fills_the_step_summary(repo, tmp_path):
    repo.commit("add", {"app.py": SIMPLE + "\n\n" + python_complex("tangle", 25)})
    summary = tmp_path / "summary.md"
    result = check(repo, "--format", "github", env={"GITHUB_STEP_SUMMARY": str(summary)})
    assert result.exit_code == 1
    assert result.stdout.startswith("::error file=app.py,")
    command = "repowise health check main...HEAD --fail-on introduced --fail-at high"
    assert result.stdout.splitlines()[-1].endswith(f"then {command}")
    text = summary.read_text(encoding="utf-8")
    assert "fails the gate" in text and f"`{command}`" in text


def test_github_format_on_a_passing_change_prints_no_reproduce_line(repo, tmp_path):
    repo.commit("docs", {"README.md": "# hi\n"})
    summary = tmp_path / "summary.md"
    result = check(repo, "--format", "github", env={"GITHUB_STEP_SUMMARY": str(summary)})
    assert result.exit_code == 0, result.output
    assert "Reproduce" not in result.stdout
    assert "Reproduce" not in summary.read_text(encoding="utf-8")


def test_sarif_and_gitlab_are_valid_documents(repo):
    repo.commit("add", {"app.py": SIMPLE + "\n\n" + python_complex("tangle", 25)})
    sarif = check(repo, "--format", "sarif")
    assert sarif.exit_code == 1
    assert json.loads(sarif.stdout)["runs"][0]["results"][0]["ruleId"] == "complex_method"
    gitlab = check(repo, "--format", "gitlab")
    assert json.loads(gitlab.stdout)[0]["severity"] == "major"


def test_unknown_revision_exits_two(repo):
    result = _split_runner().invoke(
        health_command, ["check", "nope...HEAD", "--path", str(repo.path), "--format", "json"]
    )
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"] == "unsupported_range"


def test_not_a_git_repository_exits_two(tmp_path):
    result = CliRunner().invoke(
        health_command, ["check", "--path", str(tmp_path), "--format", "gitlab"]
    )
    assert result.exit_code == 2
    assert result.stdout.strip().startswith("[]")


def test_the_report_still_takes_a_path(tmp_path, monkeypatch):
    """``repowise health PATH`` is untouched: only a subcommand name dispatches."""
    seen = {}

    def fake(path, *_args, **_kw):
        seen["path"] = path
        raise SystemExit(0)

    monkeypatch.setattr("repowise.cli.commands.health_cmd.command._resolve_repo_path", fake)
    CliRunner().invoke(health_command, [str(tmp_path)])
    assert seen["path"] == str(tmp_path)


def test_a_policy_rule_can_wrap_the_health_gate():
    from repowise.cli.commands.check_cmd import _check_gate_options, _gate_argv
    from repowise.core.policy import parse_policy

    policy = parse_policy("rules: [{name: h, gate: health, with: {fail_on: worsened}}]")
    _check_gate_options(policy)
    assert _gate_argv(policy.rules[0], "main...HEAD") == [
        "health", "check", "--fail-on=worsened", "--format", "json", "--", "main...HEAD",
    ]


TANGLE = SIMPLE + "\n\n" + python_complex("tangle", 25)
RULES = ".repowise/health-rules.json"
DISABLE = '{"disabled_biomarkers": ["complex_method"]}'


def based_on(tmp_path, rules: str) -> Repo:
    """A repo whose base commit holds *rules* as its health-rules.json."""
    r = Repo(tmp_path / "ruled")
    r.commit("base", {"app.py": SIMPLE, RULES: rules})
    git(r.path, "checkout", "-q", "-b", "feature")
    return r


def test_rules_at_the_base_apply(tmp_path):
    r = based_on(tmp_path, DISABLE)
    r.commit("add", {"app.py": TANGLE})
    assert check(r).exit_code == 0


def test_rules_the_change_writes_do_not_loosen_its_own_gate(repo):
    repo.commit("add", {"app.py": TANGLE, RULES: DISABLE})
    result = check(repo, "--format", "json")
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["gate"]["failing"][0]["rule"] == "complex_method"


def test_invalid_rules_at_the_base_exit_two(tmp_path):
    r = based_on(tmp_path, "[not json")
    r.commit("add", {"app.py": TANGLE})
    result = check(r, "--format", "json")
    assert result.exit_code == 2
    doc = json.loads(result.stdout)
    assert doc["error"] == "rules_invalid" and RULES in doc["message"]


def test_fail_at_moves_the_severity_floor(repo):
    repo.commit("add", {"app.py": TANGLE})  # CCN 51: critical
    assert check(repo, "--fail-at", "critical").exit_code == 1
    repo.commit("tame", {"app.py": SIMPLE + "\n\n" + python_complex("tangle", 6)})
    assert check(repo).exit_code == 0
    assert check(repo, "--fail-at", "medium").exit_code == 1


def test_an_added_suppression_warns_and_can_fail(repo):
    repo.commit("silence", {"app.py": SIMPLE + "import os  # noqa: F401\n"})
    result = check(repo, "--format", "json")
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["gate"]["verdict"] == "warn"
    (row,) = [f for f in doc["findings"] if f["source"] == "diff-risk"]
    assert (row["rule"], row["level"], row["line_number"]) == ("added-suppression", "warning", 3)
    assert row["fingerprint"].startswith("drf_")

    result = check(repo, "--format", "json", "--diff-risk", "added-suppression=error")
    assert result.exit_code == 1, result.output
    result = check(repo, "--format", "json", "--diff-risk", "off")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["gate"]["verdict"] == "pass"


def test_a_loosened_threshold_in_a_config_only_change_warns(repo):
    repo.commit("base cfg", {"pyproject.toml": "[tool.coverage.report]\nfail_under = 90\n"})
    git(repo.path, "branch", "-f", "main")
    repo.commit("loosen", {"pyproject.toml": "[tool.coverage.report]\nfail_under = 60\n"})
    result = check(repo, "--format", "json")
    assert result.exit_code == 0, result.output
    rules = [f["rule"] for f in json.loads(result.stdout)["findings"]]
    assert rules == ["weakened-tests"]


def test_a_bad_diff_risk_level_is_a_usage_error(repo):
    result = check(repo, "--diff-risk", "added-suppression=loud")
    assert result.exit_code == 2
    assert "--diff-risk" in result.stderr


def test_the_change_is_resolved_once(repo, monkeypatch):
    from repowise.core.analysis.change_health.sources import GitRevisionSource

    calls = []
    resolve = GitRevisionSource.resolve

    def counted(self, revspec):
        calls.append(revspec)
        return resolve(self, revspec)

    monkeypatch.setattr(GitRevisionSource, "resolve", counted)
    repo.commit("silence", {"app.py": SIMPLE + "import os  # noqa: F401\n"})
    result = check(repo, "--format", "json")
    assert result.exit_code == 0, result.output
    assert calls == ["main...HEAD"]
