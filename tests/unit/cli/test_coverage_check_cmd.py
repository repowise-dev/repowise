"""``repowise coverage check``: patch coverage of a change against a real git repo."""

from __future__ import annotations

import json
import subprocess

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli
from repowise.core.ci.base import CI_BASE_VARS


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


def _run(repo, *args: str, env: dict[str, str] | None = None):
    # Unset the CI variables the default base reads, so the host CI cannot leak in.
    base_env = dict.fromkeys(CI_BASE_VARS, "")
    return CliRunner(env={**base_env, **(env or {})}).invoke(
        cli, ["coverage", "check", "--path", str(repo), *args]
    )


def test_json_reports_patch_coverage_without_an_index(repo) -> None:
    report = _lcov(repo, {1: 1, 2: 1, 3: 0})
    result = _run(repo, "main...feat", "--report", report, "--format", "json")

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["patch_coverage_pct"] == 50.0
    assert (data["covered_line_count"], data["coverable_line_count"]) == (1, 2)
    assert data["gate"] == "not_set"
    statuses = {f["file_path"]: f["status"] for f in data["files"]}
    # The new file resolves from git (no index) and is surfaced, README is out of scope.
    assert statuses == {"src/app.py": "measured", "src/new.py": "not_in_report"}
    assert data["file_counts"]["out_of_scope"] == 1
    assert data["scope"]["label"] == "main...feat"
    assert data["scope"]["reports"] == [report]


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

    result = _run(
        repo,
        "main...feat",
        "--report",
        report,
        "--format",
        "github",
        env={"GITHUB_STEP_SUMMARY": str(summary)},
    )

    assert result.exit_code == 0, result.output
    assert "::warning file=src/app.py,line=3,endLine=3" in result.stdout
    assert summary.read_text(encoding="utf-8").startswith("**Patch coverage 50.0%**")


def test_default_base_comes_from_ci_variables(repo) -> None:
    # A CI checkout: detached, no local trunk, the base only as a remote branch.
    _git(repo, "update-ref", "refs/remotes/origin/main", "main")
    _git(repo, "checkout", "-q", "--detach", "feat")
    _git(repo, "branch", "-D", "main")
    report = _lcov(repo, {2: 1, 3: 0})

    via_ci = _run(repo, "--report", report, "--format", "json", env={"GITHUB_BASE_REF": "main"})
    assert via_ci.exit_code == 0, via_ci.output
    assert json.loads(via_ci.stdout)["scope"]["label"] == "origin/main...HEAD"
    # Without the variable, origin/main is found even though origin/HEAD is unset.
    fallback = _run(repo, "--report", report, "--format", "json")
    assert json.loads(fallback.stdout)["patch_coverage_pct"] == 50.0


def test_undeterminable_base_exits_2_instead_of_passing(repo) -> None:
    _git(repo, "checkout", "-q", "--detach", "feat")
    _git(repo, "branch", "-D", "main")
    report = _lcov(repo, {2: 1, 3: 0})

    result = _run(repo, "--report", report, "--fail-under", "90")
    assert result.exit_code == 2
    assert "Pass REVSPEC" in result.output


def test_bad_fail_under_in_config_exits_2(repo) -> None:
    (repo / ".repowise").mkdir()
    (repo / ".repowise" / "config.yaml").write_text(
        "coverage:\n  fail_under: 80%\n", encoding="utf-8"
    )
    assert _run(repo, "main...feat", "--report", _lcov(repo, {2: 1})).exit_code == 2


def test_reports_come_from_config_paths(repo) -> None:
    out = repo / "out"
    out.mkdir()
    (out / "cov.info").write_text(
        f"SF:{repo / 'src' / 'app.py'}\nDA:2,1\nDA:3,1\nend_of_record\n", encoding="utf-8"
    )
    (repo / ".repowise").mkdir()
    (repo / ".repowise" / "config.yaml").write_text(
        "coverage:\n  paths: [out/cov.info]\n", encoding="utf-8"
    )
    result = _run(repo, "main...feat", "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["patch_coverage_pct"] == 100.0


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


def test_no_merge_base_exits_2_not_1(repo) -> None:
    # An unrelated history stands in for a shallow clone that lacks the merge-base.
    _git(repo, "checkout", "-q", "--orphan", "lonely")
    _git(repo, "commit", "-qm", "orphan")
    report = _lcov(repo, {1: 1})

    result = _run(repo, "main...lonely", "--report", report)
    assert result.exit_code == 2
    assert "merge-base" in result.output


def test_flag_overrides_a_bad_config_threshold(repo) -> None:
    (repo / ".repowise").mkdir()
    (repo / ".repowise" / "config.yaml").write_text(
        "coverage:\n  fail_under: 80%\n", encoding="utf-8"
    )
    report = _lcov(repo, {2: 1, 3: 1})
    assert _run(repo, "main...feat", "--report", report, "--fail-under", "50").exit_code == 0


def test_stored_coverage_is_not_gated_when_stale_or_without_line_data() -> None:
    from repowise.cli.commands.coverage_check_cmd import _CannotEvaluateError, _gateable
    from repowise.core.analysis.health.coverage import file_coverage
    from repowise.core.analysis.patch_coverage import PatchScope, compute_patch_coverage

    def _pc(fc, freshness):
        return compute_patch_coverage(
            {"a.py": {1}}, {"a.py": fc}, threshold=80, scope=PatchScope(freshness=freshness)
        )

    fresh = _pc(file_coverage("a.py", [1], [1]), "current")
    assert _gateable(fresh) is fresh
    with pytest.raises(_CannotEvaluateError) as stale:
        _gateable(_pc(file_coverage("a.py", [1], [1]), "stale"))
    assert stale.value.code == "coverage_stale"
    with pytest.raises(_CannotEvaluateError) as legacy:
        _gateable(_pc(file_coverage("a.py", [1], []), "current"))
    assert legacy.value.code == "no_line_data"
    with pytest.raises(_CannotEvaluateError) as missing:
        _gateable(None)
    assert missing.value.code == "no_report"
