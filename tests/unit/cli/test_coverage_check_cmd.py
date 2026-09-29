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


def _check_json(repo, report: str, *, exit_code: int = 0) -> dict:
    """``coverage check main...feat --format json`` over *report*, at the expected exit code."""
    result = _run(repo, "main...feat", "--report", report, "--format", "json")
    assert result.exit_code == exit_code, result.output
    return json.loads(result.stdout)


def test_json_reports_patch_coverage_without_an_index(repo) -> None:
    report = _lcov(repo, {1: 1, 2: 1, 3: 0})
    data = _check_json(repo, report)

    assert data["patch_coverage_pct"] == 50.0
    assert (data["covered_line_count"], data["coverable_line_count"]) == (1, 2)
    assert data["gate"] == "not_set"
    statuses = {f["file_path"]: f["status"] for f in data["files"]}
    # The new file resolves from git (no index) and is surfaced, README is out of scope.
    assert statuses == {"src/app.py": "measured", "src/new.py": "not_in_report"}
    assert data["file_counts"]["out_of_scope"] == 1
    assert data["scope"]["label"] == "main...feat"
    assert data["scope"]["reports"] == [report]
    # No index, no hints: null, never an empty "nothing to suggest".
    assert all(f["hints"] is None for f in data["files"])


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


def test_small_change_tolerance_reports_but_does_not_fail(repo) -> None:
    report = _lcov(repo, {1: 1, 2: 1, 3: 0})  # 1 of 2 changed executable lines
    args = ("main...feat", "--report", report, "--fail-under", "80")

    small = _run(repo, *args, "--min-coverable-lines", "5")
    assert small.exit_code == 0, small.output
    assert "not applied: fewer than 5 changed executable lines" in small.output
    github = _run(repo, *args, "--min-coverable-lines", "5", "--format", "github")
    assert github.exit_code == 0
    assert "::notice::Patch coverage 50.0" in github.stdout
    assert _run(repo, *args, "--min-coverable-lines", "2").exit_code == 1
    assert _run(repo, *args, "--min-coverable-lines", "-1").exit_code == 2


def test_small_change_tolerance_from_config(repo) -> None:
    (repo / ".repowise").mkdir()
    config = repo / ".repowise" / "config.yaml"
    report = _lcov(repo, {2: 1, 3: 0})

    config.write_text("coverage:\n  fail_under: 80\n  min_coverable_lines: 5\n", encoding="utf-8")
    result = _run(repo, "main...feat", "--report", report, "--format", "json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert (data["gate"], data["min_coverable_lines"]) == ("too_small", 5)

    config.write_text("coverage:\n  fail_under: 80\n  min_coverable_lines: few\n", encoding="utf-8")
    bad = _run(repo, "main...feat", "--report", report, "--format", "json")
    assert bad.exit_code == 2
    assert json.loads(bad.stdout)["error"] == "config_invalid"
    # The flag stands in for a config value it cannot use.
    override = _run(repo, "main...feat", "--report", report, "--min-coverable-lines", "2")
    assert override.exit_code == 1


def test_report_globs_expand_relative_to_cwd(repo, monkeypatch) -> None:
    from pathlib import Path

    shard = repo / "artifacts" / "unit"
    shard.mkdir(parents=True)
    (shard / "lcov.info").write_text(
        f"SF:{repo / 'src' / 'app.py'}\nDA:2,1\nDA:3,1\nend_of_record\n", encoding="utf-8"
    )
    monkeypatch.chdir(repo)

    result = _run(repo, "main...feat", "--report", "artifacts/**/lcov.info", "--format", "json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["patch_coverage_pct"] == 100.0
    assert [Path(p) for p in data["scope"]["reports"]] == [Path("artifacts/unit/lcov.info")]


def test_a_report_argument_matching_no_file_exits_2(repo) -> None:
    for arg in (str(repo / "nope" / "**" / "lcov.info"), str(repo / "missing.lcov")):
        result = _run(repo, "main...feat", "--report", arg, "--format", "json")
        assert result.exit_code == 2, arg
        error = json.loads(result.stdout)
        assert error["error"] == "report_not_found"
        assert arg in error["message"]


def test_report_path_equals_prefix(repo) -> None:
    report = repo / "web.info"
    report.write_text("SF:app.py\nDA:2,1\nDA:3,1\nend_of_record\n", encoding="utf-8")

    ok = _run(repo, "main...feat", "--report", f"{report}=src", "--format", "json")
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.stdout)["patch_coverage_pct"] == 100.0
    # The prefix is applied: a wrong one leaves nothing to match.
    assert _run(repo, "main...feat", "--report", f"{report}=nowhere").exit_code == 2


def test_an_existing_path_holding_equals_is_a_path(repo) -> None:
    report = repo / "cov=v1.info"
    report.write_text(
        f"SF:{repo / 'src' / 'app.py'}\nDA:2,1\nDA:3,1\nend_of_record\n", encoding="utf-8"
    )
    result = _run(repo, "main...feat", "--report", str(report), "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["patch_coverage_pct"] == 100.0


def test_a_glob_holding_equals_stays_one_pattern(repo, monkeypatch) -> None:
    shard = repo / "artifacts" / "shard=1"
    shard.mkdir(parents=True)
    (shard / "unit.info").write_text(
        f"SF:{repo / 'src' / 'app.py'}\nDA:2,1\nDA:3,1\nend_of_record\n", encoding="utf-8"
    )
    monkeypatch.chdir(repo)

    result = _run(repo, "main...feat", "--report", "artifacts/shard=1/*.info", "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["patch_coverage_pct"] == 100.0


def test_every_report_entry_ignored_exits_2_with_its_own_message(repo) -> None:
    (repo / ".repowise").mkdir()
    (repo / ".repowise" / "config.yaml").write_text(
        "coverage:\n  ignore: [src/]\n", encoding="utf-8"
    )
    report = _lcov(repo, {2: 1, 3: 0})

    result = _run(repo, "main...feat", "--report", report, "--format", "json")
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"] == "report_all_ignored"


def test_coverage_ignore_drops_changed_files_and_report_entries(repo) -> None:
    (repo / ".repowise").mkdir()
    (repo / ".repowise" / "config.yaml").write_text(
        "coverage:\n  ignore: [src/new.py]\n", encoding="utf-8"
    )
    report = repo / "coverage.lcov"
    report.write_text(
        f"SF:{repo / 'src' / 'app.py'}\nDA:2,1\nDA:3,0\nend_of_record\n"
        f"SF:{repo / 'src' / 'new.py'}\nDA:1,0\nend_of_record\n",
        encoding="utf-8",
    )
    result = _run(repo, "main...feat", "--report", str(report), "--format", "json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert [f["file_path"] for f in data["files"]] == ["src/app.py"]
    assert data["scope"]["ignored_file_count"] == 1
    # The ignored entry is neither a match nor a miss.
    assert (data["scope"]["report_path_count"], data["scope"]["unmatched_report_path_count"]) == (
        1,
        0,
    )
@pytest.fixture
def fixed_repo(tmp_path):
    """``main`` fixed src/app.py twice and src/other.py once; ``feat`` edits app lines 2-3."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    for name in ("app", "util", "other"):
        (tmp_path / "src" / f"{name}.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    for value in ("0", "-1"):
        (tmp_path / "src" / "app.py").write_text(f"a = {value}\nb = 2\nc = 3\n", encoding="utf-8")
        _git(tmp_path, "commit", "-qam", "fix: crash on start")
    (tmp_path / "src" / "other.py").write_text("a = 0\nb = 2\nc = 3\n", encoding="utf-8")
    _git(tmp_path, "commit", "-qam", "fix: off by one")
    _git(tmp_path, "switch", "-qc", "feat")
    (tmp_path / "src" / "app.py").write_text("a = 0\nb = 22\nc = 33\n", encoding="utf-8")
    _git(tmp_path, "commit", "-qam", "feat: new values")
    return tmp_path


def test_rows_carry_git_risk_without_an_index(fixed_repo) -> None:
    report = _lcov(fixed_repo, {2: 1, 3: 0})
    result = _run(fixed_repo, "main...feat", "--report", report, "--format", "json")

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    (row,) = data["files"]
    assert row["risk"]["basis"] == "git"
    assert row["risk"]["risky"] is True
    assert row["risk"]["reasons"] == ["top quartile of files with bug-fix history"]
    assert data["risky"]["patch_coverage_pct"] == 50.0
    assert data["risky"]["gate"] == "not_set"


def test_fail_under_risky_gates_the_risky_files(fixed_repo) -> None:
    report = _lcov(fixed_repo, {2: 1, 3: 0})

    passed = _run(fixed_repo, "main...feat", "--report", report, "--fail-under-risky", "40")
    assert passed.exit_code == 0, passed.output
    failed = _run(fixed_repo, "main...feat", "--report", report, "--fail-under-risky", "80")
    assert failed.exit_code == 1
    assert "below the 80.0% risky-file gate" in failed.output
    # From config too, validated like fail_under.
    (fixed_repo / ".repowise").mkdir()
    config = fixed_repo / ".repowise" / "config.yaml"
    config.write_text("coverage:\n  fail_under_risky: 80\n", encoding="utf-8")
    assert _run(fixed_repo, "main...feat", "--report", report).exit_code == 1
    config.write_text("coverage:\n  fail_under_risky: high\n", encoding="utf-8")
    assert _run(fixed_repo, "main...feat", "--report", report).exit_code == 2


def test_fail_under_risky_without_readable_risk_exits_2(fixed_repo, monkeypatch) -> None:
    import repowise.core.analysis.patch_coverage as pc_module

    monkeypatch.setattr(pc_module, "read_git_fix_history", lambda *_a, **_k: None)
    report = _lcov(fixed_repo, {2: 1, 3: 0})

    # Without the risky gate an unreadable risk is only a gap in the table.
    assert _run(fixed_repo, "main...feat", "--report", report).exit_code == 0
    gated = _run(fixed_repo, "main...feat", "--report", report, "--fail-under-risky", "50")
    assert gated.exit_code == 2
    assert "risky-file gate cannot run" in gated.output
    # A failing flat gate is not hidden behind the risky gate's setup problem.
    both = _run(
        fixed_repo, "main...feat", "--report", report,
        "--fail-under", "80", "--fail-under-risky", "50", "--format", "json",
    )
    assert both.exit_code == 1, both.output
    assert json.loads(both.stdout)["gate"] == "fail"


def test_fail_under_risky_on_a_shallow_clone_exits_2(fixed_repo, tmp_path_factory) -> None:
    shallow = tmp_path_factory.mktemp("shallow") / "repo"
    _git(
        fixed_repo.parent, "clone", "-q", "--depth", "2", "--no-single-branch",
        fixed_repo.as_uri(), str(shallow),
    )
    report = _lcov(shallow, {2: 1, 3: 0})

    result = _run(shallow, "origin/main...HEAD", "--report", report, "--fail-under-risky", "50")
    assert result.exit_code == 2, result.output
    assert "fetch full history" in result.output.lower()


def test_an_unreadable_index_still_gates_on_git_risk(fixed_repo, monkeypatch) -> None:
    from contextlib import asynccontextmanager

    from sqlalchemy.exc import OperationalError

    import repowise.cli.commands.coverage_check_cmd as cmd
    import repowise.core.analysis.patch_coverage as pc_module

    @asynccontextmanager
    async def _opened(_root):
        yield object(), "r"

    async def _raise(*_a, **_k):
        raise OperationalError("select", {}, Exception("disk I/O error"))

    monkeypatch.setattr(cmd, "has_db_store", lambda _root: True)
    monkeypatch.setattr(cmd, "repo_index_session", _opened)
    monkeypatch.setattr(pc_module, "read_index_facts", _raise)
    report = _lcov(fixed_repo, {2: 1, 3: 0})

    result = _run(
        fixed_repo, "main...feat", "--report", report, "--fail-under-risky", "80",
        "--format", "json",
    )
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert data["files"][0]["risk"]["basis"] == "git"
    assert data["risky"]["gate"] == "fail"


def _with_index(monkeypatch, read_hints) -> None:
    from contextlib import asynccontextmanager

    import repowise.cli.commands.coverage_check_cmd as cmd
    import repowise.core.analysis.patch_coverage as pc_module

    @asynccontextmanager
    async def _opened(_root):
        yield object(), "r"

    async def _no_facts(*_a, **_k):
        return {}

    monkeypatch.setattr(cmd, "has_db_store", lambda _root: True)
    monkeypatch.setattr(cmd, "repo_index_session", _opened)
    monkeypatch.setattr(pc_module, "read_index_facts", _no_facts)
    monkeypatch.setattr(pc_module, "read_test_hints", read_hints)


def test_an_index_names_the_test_to_extend(repo, monkeypatch, tmp_path_factory) -> None:
    from repowise.core.analysis.patch_coverage import TestHint

    async def _hints(_session, _repo_id, _pc, **kwargs):
        # The checkout and head go along so stored spans can be moved to them.
        assert kwargs["repo_path"] and kwargs["head_commit"]
        return {
            "src/app.py": (
                TestHint((3, 3), "main", ("tests/test_app.py",), "call_graph", 1),
            )
        }

    _with_index(monkeypatch, _hints)
    report = _lcov(repo, {2: 1, 3: 0})
    summary = tmp_path_factory.mktemp("gh") / "summary.md"

    data = json.loads(_run(repo, "main...feat", "--report", report, "--format", "json").stdout)
    app = next(f for f in data["files"] if f["file_path"] == "src/app.py")
    assert app["hints"][0]["tests"] == ["tests/test_app.py"]
    github = _run(
        repo, "main...feat", "--report", report, "--format", "github",
        env={"GITHUB_STEP_SUMMARY": str(summary)},
    )
    assert "not covered by tests. Extend tests/test_app.py (inferred: calls reach `main`)" in (
        github.stdout
    )
    assert "extend tests/test_app.py (inferred: calls reach `main`)" in summary.read_text(
        encoding="utf-8"
    )
    table = _run(repo, "main...feat", "--report", report)
    assert "tests/test_app.py" in table.output


def test_an_unreadable_index_leaves_hints_null_and_the_gate_alone(repo, monkeypatch) -> None:
    from sqlalchemy.exc import OperationalError

    async def _raise(*_a, **_k):
        raise OperationalError("select", {}, Exception("no such table: wiki_symbols"))

    _with_index(monkeypatch, _raise)
    report = _lcov(repo, {2: 1, 3: 0})

    result = _run(repo, "main...feat", "--report", report, "--fail-under", "80", "--format", "json")
    assert result.exit_code == 1, result.output
    assert all(f["hints"] is None for f in json.loads(result.stdout)["files"])


def test_stored_coverage_is_not_gated_when_stale_or_without_line_data() -> None:
    from repowise.cli.ci import CannotEvaluateError
    from repowise.cli.commands.coverage_check_cmd import _gateable
    from repowise.core.analysis.health.coverage import file_coverage
    from repowise.core.analysis.patch_coverage import PatchScope, compute_patch_coverage

    def _pc(fc, freshness):
        return compute_patch_coverage(
            {"a.py": {1}}, {"a.py": fc}, threshold=80, scope=PatchScope(freshness=freshness)
        )

    fresh = _pc(file_coverage("a.py", [1], [1]), "current")
    assert _gateable(fresh) is fresh
    with pytest.raises(CannotEvaluateError) as stale:
        _gateable(_pc(file_coverage("a.py", [1], [1]), "stale"))
    assert stale.value.code == "coverage_stale"
    with pytest.raises(CannotEvaluateError) as legacy:
        _gateable(_pc(file_coverage("a.py", [1], []), "current"))
    assert legacy.value.code == "no_line_data"
    with pytest.raises(CannotEvaluateError) as missing:
        _gateable(None)
    assert missing.value.code == "no_report"


# -- path-scoped gates -------------------------------------------------------


_SRC_GATE = "    - {name: src, paths: [src/], fail_under: 80}\n"


def _gates_config(repo, gates: str) -> None:
    (repo / ".repowise").mkdir(exist_ok=True)
    (repo / ".repowise" / "config.yaml").write_text(
        f"coverage:\n  gates:\n{gates}", encoding="utf-8"
    )


def test_a_failing_path_gate_fails_the_check(repo) -> None:
    _gates_config(repo, _SRC_GATE)
    report = _lcov(repo, {2: 1, 3: 0})

    data = _check_json(repo, report, exit_code=1)
    # No whole-change threshold, yet the path gate fails the change.
    assert data["threshold"] is None
    assert data["gate"] == "fail"
    (gate,) = data["path_gates"]
    assert (gate["name"], gate["gate"], gate["patch_coverage_pct"]) == ("src", "fail", 50.0)
    # src/new.py changed but the report does not name it.
    assert gate["unmeasured_file_count"] == 1

    table = _run(repo, "main...feat", "--report", report)
    assert table.exit_code == 1
    assert "Fails: path-scoped gate src 50.0% (1 of 2 changed executable lines)" in table.output
    assert "Path-scoped gate" in table.output and "fails" in table.output


def test_an_informational_path_gate_never_fails_the_check(repo) -> None:
    _gates_config(
        repo, "    - {name: src, paths: [src/], fail_under: 80, informational: true}\n"
    )
    data = _check_json(repo, _lcov(repo, {2: 1, 3: 0}))
    assert data["path_gates"][0]["gate"] == "fail"


def test_github_format_errors_per_failing_path_gate(repo, tmp_path_factory) -> None:
    _gates_config(repo, _SRC_GATE + "    - {name: docs, paths: [docs/], fail_under: 80}\n")
    summary = tmp_path_factory.mktemp("gh") / "summary.md"

    result = _run(
        repo,
        "main...feat",
        "--report",
        _lcov(repo, {2: 1, 3: 0}),
        "--format",
        "github",
        env={"GITHUB_STEP_SUMMARY": str(summary)},
    )

    assert result.exit_code == 1
    errors = [line for line in result.stdout.splitlines() if line.startswith("::error::")]
    assert errors == [
        "::error::Fails: path-scoped gate src 50.0%25 (1 of 2 changed executable lines), "
        "below its 80.0%25 gate."
    ]
    assert "| `docs` | no measured changed lines | n/a | 80.0% |" in summary.read_text(
        encoding="utf-8"
    )


def test_an_invalid_path_gate_exits_2_naming_it(repo) -> None:
    _gates_config(repo, "    - {name: src, paths: [src/], fail_under: high}\n")

    result = _run(repo, "main...feat", "--report", _lcov(repo, {2: 1}), "--format", "json")
    assert result.exit_code == 2
    data = json.loads(result.stdout)
    assert data["error"] == "config_invalid"
    assert data["message"].startswith("coverage.gates[0] ('src'): fail_under must be")


def test_stored_coverage_is_judged_by_the_configured_path_gates(monkeypatch, tmp_path) -> None:
    import contextlib

    from repowise.cli.commands import coverage_check_cmd
    from repowise.core.analysis import patch_coverage
    from repowise.core.analysis.health.coverage import CoverageConfig, PathGate

    @contextlib.asynccontextmanager
    async def _session(root):
        yield object(), "repo-id"

    seen = {}

    async def _stored(session, repo_id, changed, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(coverage_check_cmd, "repo_index_session", _session)
    monkeypatch.setattr(patch_coverage, "stored_patch_coverage", _stored)
    cfg = CoverageConfig(ignore=("gen/",), gates=(PathGate("api", ("api/",), 80),))

    run = coverage_check_cmd._stored(tmp_path, {"a.py": {1}}, "HEAD", None, 3, cfg)
    coverage_check_cmd.run_async(run)
    assert seen["config"].gates == cfg.gates
    assert seen["config"].ignore == ("gen/",)
    assert seen["config"].min_coverable_lines == 3


# -- coverage suggest-gates --------------------------------------------------


@pytest.fixture
def layout_repo(tmp_path):
    """A monorepo with CODEOWNERS, two packages, tests and docs."""
    _git(tmp_path, "init", "-q", "-b", "main")
    files = {
        ".github/CODEOWNERS": (
            "# owners\n* @org/all\n/packages/api/ @org/backend\n"
            "/packages/api/web/ @org/frontend\n*.md @org/docs\n"
        ),
        "packages/api/src/app.py": "x = 1\n",
        "packages/api/web/view.ts": "export const x = 1;\n",
        "packages/ui/src/button.tsx": "export {};\n",
        "packages/ui/README.md": "docs\n",
        "tests/test_app.py": "def test(): pass\n",
        "docs/guide.md": "guide\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    _git(tmp_path, "add", "-A")
    return tmp_path


def _suggest(repo, *args: str):
    return CliRunner().invoke(cli, ["coverage", "suggest-gates", "--path", str(repo), *args])


def test_suggest_prints_yaml_from_codeowners_and_packages_without_an_index(layout_repo) -> None:
    import yaml

    from repowise.core.analysis.health.coverage import CoverageConfig

    result = _suggest(layout_repo)

    assert result.exit_code == 0, result.output
    text = result.stdout
    assert "# From CODEOWNERS: .github/CODEOWNERS" in text
    assert "# From top-level packages: git ls-files" in text
    assert "# From graph communities: unavailable, no index" in text
    # Indented to paste directly below the coverage: line.
    config = yaml.safe_load("coverage:\n" + text)
    gates = {g["name"]: g for g in config["coverage"]["gates"]}
    # A later pattern owned by someone else that may reach into a gate's files
    # is carried as an exclusion (``*.md`` matches at any depth); the leading
    # catch-all owner gets no gate.
    assert gates["org-backend"]["paths"] == ["/packages/api/", "!/packages/api/web/", "!*.md"]
    assert gates["org-frontend"]["paths"] == ["/packages/api/web/", "!*.md"]
    assert gates["org-docs"]["paths"] == ["*.md"]
    assert "org-all" not in gates
    assert gates["api"]["paths"] == ["/packages/api/"]
    assert gates["ui"]["paths"] == ["/packages/ui/"]
    # Suggestions pick no threshold, and paste into a valid config.
    assert all("fail_under" not in g for g in gates.values())
    cfg = CoverageConfig.from_repo_config(config)
    assert cfg.gate_errors == ()
    assert len(cfg.gates) == len(gates)


def test_suggest_json_and_the_graph_source(layout_repo, monkeypatch) -> None:
    from repowise.cli.commands import coverage_suggest_gates_cmd as cmd

    async def _graph(root):
        return {
            "packages/api/src/app.py": 1,
            "packages/api/src/db.py": 1,
            "packages/api/src/models.py": 1,
            # Same globs as the ``ui`` package gate: not suggested twice.
            "packages/ui/a.ts": 2,
            "packages/ui/b.ts": 2,
            "packages/ui/c.ts": 2,
            "packages/ui/src/button.tsx": 3,
        }, "0123456789abcdef"

    monkeypatch.setattr(cmd, "_read_graph", _graph)
    result = _suggest(layout_repo, "--format", "json")

    assert result.exit_code == 0, result.output
    sources = {s["source"]: s for s in json.loads(result.stdout)["sources"]}
    assert list(sources) == ["codeowners", "layout", "graph"]
    assert sources["graph"]["detail"] == "the index at 0123456"
    # Named by its common directory; ``src`` is unique across sources, the
    # tiny community is skipped and the one repeating a package gate dropped.
    assert sources["graph"]["gates"] == [{"name": "src", "paths": ["/packages/api/src/"]}]


def test_suggest_graph_source_without_communities_or_readable_index(
    layout_repo, monkeypatch
) -> None:
    import contextlib

    from repowise.cli.commands import coverage_suggest_gates_cmd as cmd

    async def _flat(root):
        return {f"packages/api/src/{n}.py": 0 for n in "abcd"}, None

    monkeypatch.setattr(cmd, "_read_graph", _flat)
    flat = json.loads(_suggest(layout_repo, "--format", "json").stdout)["sources"][2]
    assert flat == {"source": "graph", "detail": "the index has no communities computed", "gates": []}

    @contextlib.asynccontextmanager
    async def _broken(root):
        yield None

    # A store that exists but cannot be opened is unreadable, not absent.
    monkeypatch.undo()
    monkeypatch.setattr("repowise.core.persistence.database.has_db_store", lambda root: True)
    monkeypatch.setattr(cmd, "repo_index_session", _broken)
    broken = json.loads(_suggest(layout_repo, "--format", "json").stdout)["sources"][2]
    assert broken["detail"] == "index unreadable, so none suggested"


def test_suggest_without_codeowners_uses_top_level_directories(tmp_path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    for rel in ("lib/core.py", "scripts/run.sh", "tests/test_core.py", "setup.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")

    result = _suggest(tmp_path, "--format", "json")
    sources = {s["source"]: s for s in json.loads(result.stdout)["sources"]}
    assert sources["codeowners"] == {
        "source": "codeowners",
        "detail": "no CODEOWNERS file",
        "gates": [],
    }
    assert sources["layout"]["gates"] == [{"name": "lib", "paths": ["/lib/"]}]


def test_suggest_reads_a_gitlab_codeowners(tmp_path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / ".gitlab").mkdir()
    (tmp_path / ".gitlab" / "CODEOWNERS").write_text("[API] @api\n/api/\n", encoding="utf-8")

    sources = json.loads(_suggest(tmp_path, "--format", "json").stdout)["sources"]
    assert sources[0]["detail"] == ".gitlab/CODEOWNERS"
    assert sources[0]["gates"] == [{"name": "api", "paths": ["/api/"]}]
