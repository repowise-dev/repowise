"""``repowise security check``: the index-free CI gate and its exit codes."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from repowise.cli.commands.security_cmd import security_command
from tests.unit.analysis.test_security_gate_extras import KEY, _git, _write, init_repo
from tests.unit.analysis.test_security_gate_extras import _commit as _commit_all
from tests.unit.cli.test_format_json_rollout import _split_runner


def _commit(repo, name: str, text: str) -> None:
    _write(repo, name, text)
    _commit_all(repo, name)


@pytest.fixture
def repo(tmp_path):
    return init_repo(tmp_path)


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


def test_gitlab_format_is_one_issue_list(repo):
    _commit(repo, "run.py", "import os\nos.system(cmd)\n")
    result = _check(repo, "--format", "gitlab")
    assert result.exit_code == 1
    (issue,) = json.loads(result.stdout)
    assert issue["check_name"] == "repowise-security/os_system"
    assert issue["severity"] == "critical"
    assert issue["location"] == {"path": "run.py", "lines": {"begin": 2}}


def test_gitlab_severity_follows_a_non_default_fail_on(repo):
    _commit(repo, "h.py", "import hashlib\nhashlib.md5(b'')\n")
    (default,) = json.loads(_check(repo, "--format", "gitlab").stdout)
    assert default["severity"] == "minor"
    result = _check(repo, "--fail-on", "low", "--format", "gitlab")
    assert result.exit_code == 1
    (issue,) = json.loads(result.stdout)
    assert issue["severity"] == "major"


def test_gitlab_leaves_baselined_findings_out(repo, tmp_path):
    _commit(repo, "h.py", "import hashlib\nhashlib.md5(b'')\n")
    baseline = tmp_path / "b.json"
    assert _check(repo, "--write-baseline", str(baseline)).exit_code == 0
    result = _check(repo, "--baseline", str(baseline), "--format", "gitlab")
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_gitlab_prints_an_empty_list_when_it_cannot_evaluate(tmp_path):
    result = _split_runner().invoke(
        security_command, ["check", "--path", str(tmp_path), "--format", "gitlab"]
    )
    assert result.exit_code == 2
    assert json.loads(result.stdout) == []


def _staged(repo, *args: str):
    return _split_runner().invoke(
        security_command, ["check", "--staged", "--path", str(repo), *args]
    )


def _stage(repo, name: str, text: str) -> None:
    (repo / name).write_text(text, encoding="utf-8", newline="\n")
    _git(repo, "add", name)


def test_staged_fails_on_a_staged_secret_and_ignores_unstaged_ones(repo):
    _stage(repo, "staged.py", f"AWS = '{KEY}'\n")
    (repo / "loose.py").write_text(f"AWS = '{KEY}'\n", encoding="utf-8", newline="\n")
    result = _staged(repo, "--format", "json")
    assert result.exit_code == 1, result.output
    doc = json.loads(result.stdout)
    assert doc["revspec"] == "staged changes" and doc["commits_scanned"] == 0
    assert [f["file_path"] for f in doc["findings"]] == ["staged.py"]


def test_staged_passes_once_the_line_carries_the_marker(repo):
    _stage(repo, "staged.py", f"AWS = '{KEY}'  # repowise-security-ignore\n")
    result = _staged(repo, "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["gate"]["suppressed_count"] == 1


def test_staged_with_a_revspec_is_a_usage_error(repo):
    result = _split_runner().invoke(
        security_command,
        ["check", "main...HEAD", "--staged", "--path", str(repo), "--format", "json"],
    )
    assert result.exit_code == 2
    assert "drop REVSPEC" in result.stderr


def _config(repo, text: str) -> None:
    (repo / ".repowise").mkdir(exist_ok=True)
    (repo / ".repowise" / "config.yaml").write_text(text, encoding="utf-8")


def test_an_invalid_pattern_config_exits_two_listing_every_error(repo):
    _config(
        repo,
        "security:\n  patterns:\n    - name: a\n      regex: '('\n"
        "    - name: b\n      regex: 'x*'\n",
    )
    result = _check(repo, "--format", "json")
    assert result.exit_code == 2
    doc = json.loads(result.stdout)
    assert doc["error"] == "config_invalid"
    assert "patterns[0]" in doc["message"] and "patterns[1]" in doc["message"]


def test_a_custom_pattern_from_config_gates_the_change(repo):
    token = "itk_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
    _config(
        repo,
        "security:\n  patterns:\n    - name: internal_token\n      regex: 'itk_[A-Za-z0-9]{32}'\n",
    )
    _commit(repo, "a.py", f"T = '{token}'\n")
    result = _check(repo, "--format", "json")
    assert result.exit_code == 1, result.output
    (finding,) = json.loads(result.stdout)["findings"]
    assert finding["kind"] == "custom:internal_token" and token not in result.output


def test_gitlab_counts_suppressed_findings_on_stderr_only(repo):
    _commit(repo, "cfg.py", f"AWS = '{KEY}'  # repowise-security-ignore\n")
    result = _split_runner().invoke(
        security_command, ["check", "main...HEAD", "--path", str(repo), "--format", "gitlab"]
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []
    assert "1 finding(s) suppressed inline" in result.stderr


def test_the_table_lists_suppressed_findings(repo):
    _commit(repo, "cfg.py", f"AWS = '{KEY}'  # repowise-security-ignore\n")
    result = _check(repo)
    assert result.exit_code == 0
    assert "1 finding(s) suppressed inline" in result.output and "cfg.py:1" in result.output


def test_an_unexpected_crash_cannot_evaluate_rather_than_fail(repo, monkeypatch):
    from repowise.core.analysis import security_gate

    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(security_gate, "scan_change", boom)
    _stage(repo, "a.py", "x = 3\n")
    for fmt in ("json", "table", "gitlab"):
        result = _staged(repo, "--format", fmt)
        assert result.exit_code == 2, (fmt, result.output)
    assert json.loads(_staged(repo, "--format", "json").stdout)["error"] == "internal_error"


def test_suppressed_findings_stay_out_of_a_written_baseline(repo, tmp_path):
    _commit(repo, "cfg.py", f"AWS = '{KEY}'  # repowise-security-ignore\nimport os\nos.system(c)\n")
    baseline = tmp_path / "b.json"
    result = _check(repo, "--write-baseline", str(baseline), "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["recorded"] == 1
    entries = json.loads(baseline.read_text(encoding="utf-8"))["entries"]
    assert [e["kind"] for e in entries] == ["os_system"]
