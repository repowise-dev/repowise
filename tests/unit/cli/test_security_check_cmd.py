"""``repowise security check``: the index-free CI gate and its exit codes."""

from __future__ import annotations

import json
import subprocess

import pytest
from click.testing import CliRunner

from repowise.cli.commands.security_cmd import security_command

KEY = "AKIA" + "QZXNRTVYWMPKLBHG"


def _git(cwd, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repo, name: str, text: str) -> None:
    (repo / name).write_text(text, encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", name)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "t")
    _commit(tmp_path, "app.py", "x = 1\n")
    _git(tmp_path, "switch", "-qc", "feature")
    return tmp_path


def _check(repo, *args: str, env=None):
    return CliRunner().invoke(
        security_command,
        ["check", "main...HEAD", "--path", str(repo), *args],
        env=env or {},
    )


def test_a_clean_change_exits_zero(repo):
    _commit(repo, "app.py", "x = 2\n")
    result = _check(repo)
    assert result.exit_code == 0, result.output
    assert "Gate passed" in result.output


def test_a_secret_deleted_inside_the_change_exits_one(repo):
    _commit(repo, "cfg.py", f"AWS = '{KEY}'\n")
    _commit(repo, "cfg.py", "AWS = None\n")
    result = _check(repo, "--format", "json")
    assert result.exit_code == 1
    doc = json.loads(result.stdout)
    (finding,) = doc["findings"]
    assert finding["kind"] == "aws_access_key" and finding["commit"]
    assert doc["gate"]["passed"] is False and doc["commits_scanned"] == 2
    assert KEY not in result.output


def test_fail_on_decides_what_fails(repo):
    _commit(repo, "h.py", "import hashlib\nhashlib.md5(b'')\n")
    assert _check(repo).exit_code == 0
    assert _check(repo, "--fail-on", "low").exit_code == 1


def test_unknown_revision_exits_two_with_a_json_document(repo):
    result = CliRunner().invoke(
        security_command, ["check", "nope...HEAD", "--path", str(repo), "--format", "json"]
    )
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"] == "diff_failed"


def test_not_a_git_repository_exits_two(tmp_path):
    result = CliRunner().invoke(
        security_command, ["check", "--path", str(tmp_path), "--format", "json"]
    )
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"] == "not_a_git_repository"


def test_unreadable_baseline_exits_two(repo, tmp_path):
    bad = tmp_path / "baseline.json"
    bad.write_text("not json", encoding="utf-8")
    result = _check(repo, "--baseline", str(bad), "--format", "json")
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"] == "baseline_unreadable"


def test_written_baseline_accepts_the_findings(repo, tmp_path):
    _commit(repo, "cfg.py", f"AWS = '{KEY}'\n")
    baseline = tmp_path / "baseline.json"
    assert _check(repo, "--write-baseline", str(baseline)).exit_code == 0
    assert KEY not in baseline.read_text(encoding="utf-8")
    result = _check(repo, "--baseline", str(baseline))
    assert result.exit_code == 0, result.output


def test_github_format_annotates_and_fills_the_step_summary(repo, tmp_path):
    _commit(repo, "run.py", "import os\nos.system(cmd)\n")
    summary = tmp_path / "summary.md"
    result = _check(repo, "--format", "github", env={"GITHUB_STEP_SUMMARY": str(summary)})
    assert result.exit_code == 1
    assert result.stdout.startswith("::error file=run.py,line=2,")
    assert "a floor, not a scanner" in summary.read_text(encoding="utf-8")


def test_sarif_format_is_one_log(repo):
    _commit(repo, "run.py", "import os\nos.system(cmd)\n")
    result = _check(repo, "--format", "sarif")
    assert result.exit_code == 1
    log = json.loads(result.stdout)
    assert log["runs"][0]["results"][0]["ruleId"] == "os_system"


def test_write_baseline_merges_the_given_baseline_and_speaks_json(repo, tmp_path):
    _commit(repo, "cfg.py", f"AWS = '{KEY}'\n")
    old = tmp_path / "old.json"
    old.write_text(
        '{"version": 1, "entries": [{"fingerprint": "f" }]}', encoding="utf-8"
    )
    new = tmp_path / "new.json"
    result = _check(
        repo, "--baseline", str(old), "--write-baseline", str(new), "--format", "json"
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"baseline": str(new), "recorded": 1, "entries": 2}


def test_sarif_suppresses_baselined_findings_below_the_threshold(repo, tmp_path):
    _commit(repo, "h.py", "import hashlib\nhashlib.md5(b'')\n")
    baseline = tmp_path / "b.json"
    assert _check(repo, "--write-baseline", str(baseline)).exit_code == 0
    result = _check(repo, "--baseline", str(baseline), "--format", "sarif")
    (res,) = json.loads(result.stdout)["runs"][0]["results"]
    assert res["ruleId"] == "weak_hash" and res["suppressions"] == [{"kind": "external"}]
