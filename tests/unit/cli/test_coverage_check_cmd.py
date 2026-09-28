"""``repowise coverage check``: patch coverage of a change against a real git repo."""

from __future__ import annotations

import json
import subprocess

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli


def _git(cwd, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """``main`` has src/app.py; branch ``feat`` edits lines 2-3 and adds src/new.py."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    _git(tmp_path, "switch", "-qc", "feat")
    (tmp_path / "src" / "app.py").write_text("a = 1\nb = 22\nc = 33\n", encoding="utf-8")
    (tmp_path / "src" / "new.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("docs\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "feat")
    return tmp_path


def _lcov(repo, covered: dict[int, int]) -> str:
    """An lcov report for src/app.py with absolute paths, as runners write them."""
    body = "".join(f"DA:{line},{hits}\n" for line, hits in covered.items())
    path = repo / "coverage.lcov"
    path.write_text(f"SF:{repo / 'src' / 'app.py'}\n{body}end_of_record\n", encoding="utf-8")
    return str(path)


def _run(repo, *args: str):
    return CliRunner().invoke(cli, ["coverage", "check", "--path", str(repo), *args])


def test_json_reports_patch_coverage_without_an_index(repo) -> None:
    report = _lcov(repo, {1: 1, 2: 1, 3: 0})
    result = _run(repo, "main...feat", "--report", report, "--format", "json")

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["patch_coverage_pct"] == 50.0
    assert (data["covered_lines"], data["coverable_lines"]) == (1, 2)
    assert data["gate"] == "not_set"
    statuses = {f["path"]: f["status"] for f in data["files"]}
    # The new file resolves from git (no index) and is surfaced, README is out of scope.
    assert statuses == {"src/app.py": "measured", "src/new.py": "not_in_report"}
    assert data["file_counts"]["out_of_scope"] == 1
    assert data["scope"]["label"] == "main...feat"


def test_gate_fails_below_threshold(repo) -> None:
    report = _lcov(repo, {1: 1, 2: 1, 3: 0})

    assert _run(repo, "main...feat", "--report", report, "--fail-under", "50").exit_code == 0
    failed = _run(repo, "main...feat", "--report", report, "--fail-under", "80")
    assert failed.exit_code == 1
    assert "below the 80.0% gate" in failed.output


def test_threshold_defaults_to_repo_config(repo) -> None:
    (repo / ".repowise").mkdir()
    (repo / ".repowise" / "config.yaml").write_text(
        "coverage:\n  fail_under: 90\n", encoding="utf-8"
    )
    report = _lcov(repo, {2: 1, 3: 0})

    assert _run(repo, "main...feat", "--report", report).exit_code == 1


def test_github_format_writes_annotations_and_step_summary(repo, tmp_path_factory) -> None:
    report = _lcov(repo, {2: 1, 3: 0})
    summary = tmp_path_factory.mktemp("gh") / "summary.md"

    result = CliRunner(env={"GITHUB_STEP_SUMMARY": str(summary)}).invoke(
        cli,
        ["coverage", "check", "--path", str(repo), "main...feat", "--report", report,
         "--format", "github"],
    )

    assert result.exit_code == 0, result.output
    assert "::warning file=src/app.py,line=3,endLine=3" in result.stdout
    assert summary.read_text(encoding="utf-8").startswith("**Patch coverage 50.0%**")


def test_cannot_evaluate_exits_2(repo, tmp_path) -> None:
    # No report given and none discoverable.
    assert _run(repo, "main...feat").exit_code == 2
    report = _lcov(repo, {1: 1})
    assert _run(repo, "nope...feat", "--report", report).exit_code == 2
    # A report whose paths match nothing in the repository.
    other = tmp_path / "other.lcov"
    other.write_text("SF:/elsewhere/zzz.py\nDA:1,1\nend_of_record\n", encoding="utf-8")
    assert _run(repo, "main...feat", "--report", str(other)).exit_code == 2


def test_not_a_git_repository_exits_2(tmp_path) -> None:
    lcov = tmp_path / "c.lcov"
    lcov.write_text("SF:a.py\nDA:1,1\nend_of_record\n", encoding="utf-8")
    assert _run(tmp_path, "--report", str(lcov)).exit_code == 2
