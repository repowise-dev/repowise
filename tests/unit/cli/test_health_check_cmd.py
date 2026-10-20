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
    assert "fails the gate" in summary.read_text(encoding="utf-8")


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
