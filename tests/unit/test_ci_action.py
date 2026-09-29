"""The GitHub Action, its runner script and the CI guide stay in step.

``action.yml`` passes inputs to ``ci/github/run-gates.sh`` as environment
variables and the guide documents every input, so a renamed input that one of
the three misses would fail silently in a user's workflow. The runner is also
run here against a stand-in ``repowise`` to pin its exit-code rules.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / "action.yml"
RUNNER = ROOT / "ci" / "github" / "run-gates.sh"
GUIDE = ROOT / "docs" / "start" / "CI.md"
GITLAB = ROOT / "ci" / "gitlab" / "repowise.gitlab-ci.yml"


def _action() -> dict:
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))


def test_every_input_is_documented_in_the_guide() -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    missing = [name for name in _action()["inputs"] if f"| `{name}` |" not in guide]
    assert not missing


def test_every_variable_the_runner_reads_is_passed_by_the_action() -> None:
    step = next(s for s in _action()["runs"]["steps"] if s.get("id") == "gates")
    passed = set(step["env"])
    script = RUNNER.read_text(encoding="utf-8")
    read = set(re.findall(r"\$\{?([A-Z][A-Z_]+)", script))
    runner_env = {"GITHUB_OUTPUT", "GITHUB_BASE_REF"}
    for_python = {"PYTHONUTF8"}
    assert read - runner_env <= passed
    assert passed - for_python <= read


def test_the_gitlab_template_parses() -> None:
    jobs = yaml.safe_load(GITLAB.read_text(encoding="utf-8"))
    assert {
        "repowise-coverage",
        "repowise-doc-drift",
        "repowise-security",
        "repowise-risk",
    } <= set(jobs)
    risk = " ".join(jobs["repowise-risk"]["script"])
    assert "--fail-above-percentile" in risk and "--format markdown" in risk
    assert "REPOWISE_RISK_FAIL_ABOVE_PERCENTILE" in jobs["repowise-risk"]["rules"][0]["if"]
    coverage = " ".join(jobs["repowise-coverage"]["script"])
    assert "--fail-under-risky" in coverage
    assert "--fail-under-branches" in coverage
    assert "--base-report" in coverage and "--max-drop" in coverage


def _gitlab_jobs() -> dict:
    return yaml.safe_load(GITLAB.read_text(encoding="utf-8"))


@pytest.mark.parametrize("gate", ["doc-drift", "security"])
def test_the_gitlab_code_quality_report_is_the_file_the_job_writes(gate) -> None:
    job = _gitlab_jobs()[f"repowise-{gate}"]
    report = f"gl-code-quality-{gate}.json"
    assert job["artifacts"]["reports"]["codequality"] == report
    assert f"repowise-{gate}.md" in job["artifacts"]["paths"]
    (block,) = job["script"]
    assert f"mv gl.tmp {report}" in block and f"echo '[]' > {report}" in block


def test_the_default_branch_publishes_the_reports_the_widget_compares_against() -> None:
    job = _gitlab_jobs()["repowise-code-quality"]
    assert "extends" not in job and job["allow_failure"] is True
    assert job["rules"] == [{"if": "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH"}]
    # One path: the report type takes a single file.
    assert job["artifacts"]["reports"]["codequality"] == "gl-code-quality-doc-drift.json"


_ISSUE = '[{"check_name": "repowise-security/eval_call"}]'


def _run_gitlab_job(tmp_path: Path, bin_dir: Path, job_name: str, **env: str) -> int:
    """Run a job's script the way GitLab does: one shell under ``set -eo pipefail``."""
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not available")
    # Out of the install line only: the fake stands in for the installed CLI.
    lines = [
        line
        for block in _gitlab_jobs()[job_name]["script"]
        for line in block.splitlines()
        if "pip install" not in line
    ]
    python_dir = str(Path(sys.executable).parent)
    full = {
        **os.environ,
        "PATH": os.pathsep.join([str(bin_dir), python_dir, os.environ["PATH"]]),
        "FAKE_LOG": str(tmp_path / "log"),
        "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "main",
        **env,
    }
    script = "\n".join(["set -eo pipefail", *lines]) + "\n"
    proc = subprocess.run([bash, "-c", script], cwd=tmp_path, env=full, capture_output=True)
    return proc.returncode


@pytest.mark.parametrize(
    "case",
    [
        # (gate, markdown run's exit, gitlab run's exit, gitlab run's stdout, artifact)
        ("doc-drift", "0", "0", "[]", []),
        ("security", "1", "1", _ISSUE, json.loads(_ISSUE)),  # the gate failed
        ("security", "2", "2", "[]", []),  # could not evaluate
        ("doc-drift", "1", "0", _ISSUE, json.loads(_ISSUE)),  # the runs disagree
        ("doc-drift", "0", "1", "Traceback (most recent call last):", []),  # a crash
        ("security", "0", "2", "", []),  # a usage error prints nothing
    ],
)
def test_the_gitlab_job_writes_a_valid_report_and_exits_with_the_gate(
    tmp_path, fake_repowise, case
) -> None:
    gate, markdown_code, gitlab_code, gitlab_out, report = case
    code = _run_gitlab_job(
        tmp_path,
        fake_repowise,
        f"repowise-{gate}",
        FAKE_DOC_DRIFT=markdown_code,
        FAKE_SECURITY=markdown_code,
        FAKE_GITLAB=gitlab_code,
        FAKE_GITLAB_OUT=gitlab_out,
    )
    assert code == int(markdown_code)
    first, second = (tmp_path / "log").read_text(encoding="utf-8").splitlines()
    assert first.endswith("--format markdown") and second.endswith("--format gitlab")
    assert first.removesuffix("markdown") == second.removesuffix("gitlab")
    artifact = tmp_path / f"gl-code-quality-{gate}.json"
    assert json.loads(artifact.read_text(encoding="utf-8")) == report


def test_the_default_branch_job_writes_the_doc_drift_report(tmp_path, fake_repowise) -> None:
    code = _run_gitlab_job(
        tmp_path, fake_repowise, "repowise-code-quality", FAKE_DOC_DRIFT="1",
        FAKE_GITLAB_OUT=_ISSUE,
    )
    assert code == 0
    (call,) = (tmp_path / "log").read_text(encoding="utf-8").splitlines()
    assert call.startswith("doc-drift --check") and call.endswith("--format gitlab")
    doc_drift = tmp_path / "gl-code-quality-doc-drift.json"
    assert json.loads(doc_drift.read_text(encoding="utf-8")) == json.loads(_ISSUE)


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("", "coverage check origin/main...HEAD --report a.info --format markdown"),
        (
            "base/a.info base/b.info",
            "coverage check origin/main...HEAD --report a.info --base-report base/a.info "
            "--base-report base/b.info --max-drop 0.5 --format markdown",
        ),
    ],
)
def test_the_gitlab_coverage_job_passes_base_reports_only_when_set(
    tmp_path, fake_repowise, base, expected
) -> None:
    env = {"REPOWISE_COVERAGE_REPORT": "a.info", "REPOWISE_COVERAGE_BASE_REPORT": base}
    if base:
        env["REPOWISE_COVERAGE_MAX_DROP"] = "0.5"
    code = _run_gitlab_job(tmp_path, fake_repowise, "repowise-coverage", **env)

    assert code == 0
    assert (tmp_path / "log").read_text(encoding="utf-8").split() == expected.split()


def test_the_gitlab_coverage_job_leaves_globs_to_repowise() -> None:
    script = yaml.safe_load(GITLAB.read_text(encoding="utf-8"))["repowise-coverage"]["script"]
    # Globbing off before the unquoted, space-split report list is expanded.
    assert script[0] == "set -f"
    assert "--min-coverable-lines" in script[1]


@pytest.fixture
def fake_repowise(tmp_path: Path) -> Path:
    """A ``repowise`` that logs its arguments and exits with ``$FAKE_<GATE>``.

    A ``--format gitlab`` run prints ``$FAKE_GITLAB_OUT`` (default ``[]``) and
    exits with ``$FAKE_GITLAB`` when that is set.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "repowise"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'echo "$*" >> "$FAKE_LOG"\n'
        'case "$*" in *"--format gitlab"*)\n'
        '  printf "%s" "${FAKE_GITLAB_OUT-[]}"\n'
        '  if [ -n "${FAKE_GITLAB:-}" ]; then exit "$FAKE_GITLAB"; fi ;;\n'
        "esac\n"
        'case "$1" in\n'
        '  coverage) exit "${FAKE_COVERAGE:-0}" ;;\n'
        '  doc-drift) exit "${FAKE_DOC_DRIFT:-0}" ;;\n'
        '  security) exit "${FAKE_SECURITY:-0}" ;;\n'
        '  risk) exit "${FAKE_RISK:-0}" ;;\n'
        "esac\n",
        encoding="utf-8",
        newline="\n",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _bash() -> str | None:
    """A real bash; on Windows the System32 one is the WSL launcher, which cannot run this."""
    git_bash = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Git", "bin", "bash.exe")
    if os.name == "nt" and git_bash.exists():
        return str(git_bash)
    found = shutil.which("bash")
    if found and "system32" in found.lower():
        return None
    return found


def _run(tmp_path: Path, bin_dir: Path, **env: str) -> tuple[int, str, str]:
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not available")
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    out = tmp_path / "out"
    log = tmp_path / "log"
    full = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(out),
        "GITHUB_BASE_REF": "",
        "FAKE_LOG": str(log),
        "CHECKS": "coverage,doc-drift,security",
        "BASE": "",
        "COVERAGE_REPORT": "",
        "COVERAGE_FAIL_UNDER": "",
        "COVERAGE_MIN_COVERABLE_LINES": "",
        "COVERAGE_FAIL_UNDER_RISKY": "",
        "COVERAGE_FAIL_UNDER_BRANCHES": "",
        "COVERAGE_BASE_REPORT": "",
        "COVERAGE_MAX_DROP": "",
        "DOC_DRIFT_BASELINE": "",
        "SECURITY_FAIL_ON": "high",
        "SECURITY_BASELINE": "",
        "RISK_FAIL_ABOVE_PERCENTILE": "",
        "SARIF": "false",
        "SARIF_DIR": str(tmp_path / "sarif"),
        **env,
    }
    proc = subprocess.run([bash, str(RUNNER)], cwd=repo, env=full, capture_output=True, text=True)
    return (
        proc.returncode,
        out.read_text(encoding="utf-8") if out.exists() else "",
        log.read_text(encoding="utf-8") if log.exists() else "",
    )


def test_every_gate_runs_and_a_failure_outranks_a_setup_error(tmp_path, fake_repowise) -> None:
    code, outputs, calls = _run(tmp_path, fake_repowise, FAKE_COVERAGE="2", FAKE_SECURITY="1")
    assert code == 1
    assert outputs.split() == ["coverage=2", "doc-drift=0", "security=1"]
    assert len(calls.splitlines()) == 3


def test_a_setup_error_alone_exits_two(tmp_path, fake_repowise) -> None:
    code, _, _ = _run(tmp_path, fake_repowise, CHECKS="doc-drift", FAKE_DOC_DRIFT="2")
    assert code == 2


def test_inputs_reach_the_commands(tmp_path, fake_repowise) -> None:
    code, _, calls = _run(
        tmp_path,
        fake_repowise,
        CHECKS="coverage, security",
        BASE="origin/main...HEAD",
        COVERAGE_REPORT="a.info\nartifacts/**/lcov.info=web",
        COVERAGE_FAIL_UNDER="80",
        COVERAGE_MIN_COVERABLE_LINES="5",
        SECURITY_BASELINE=".security-baseline.json",
    )
    assert code == 0
    coverage, security = calls.splitlines()
    # The glob reaches repowise unexpanded, with its prefix.
    assert coverage == (
        "coverage check origin/main...HEAD --format github "
        "--report a.info --report artifacts/**/lcov.info=web --fail-under 80 "
        "--min-coverable-lines 5"
    )
    assert security == (
        "security check origin/main...HEAD --fail-on high "
        "--baseline .security-baseline.json --format github"
    )


def test_base_report_inputs_reach_the_coverage_command(tmp_path, fake_repowise) -> None:
    code, _, calls = _run(
        tmp_path,
        fake_repowise,
        CHECKS="coverage",
        COVERAGE_REPORT="lcov.info",
        COVERAGE_BASE_REPORT="base/lcov.info\nbase/web/**/lcov.info=web",
        COVERAGE_MAX_DROP="0.5",
    )
    assert code == 0
    assert calls.strip() == (
        "coverage check --format github --report lcov.info "
        "--base-report base/lcov.info --base-report base/web/**/lcov.info=web --max-drop 0.5"
    )


def test_branch_input_reaches_the_coverage_command(tmp_path, fake_repowise) -> None:
    code, _, calls = _run(
        tmp_path, fake_repowise, CHECKS="coverage", COVERAGE_FAIL_UNDER_BRANCHES="70"
    )
    assert code == 0
    assert calls.strip() == "coverage check --format github --fail-under-branches 70"


def test_risk_inputs_reach_the_commands(tmp_path, fake_repowise) -> None:
    code, outputs, calls = _run(
        tmp_path,
        fake_repowise,
        CHECKS="coverage,risk",
        COVERAGE_FAIL_UNDER_RISKY="90",
        RISK_FAIL_ABOVE_PERCENTILE="95",
        FAKE_RISK="1",
    )
    assert code == 1
    assert outputs.split() == ["coverage=0", "risk=1"]
    coverage, risk = calls.splitlines()
    assert coverage == "coverage check --format github --fail-under-risky 90"
    # Gated, the CLI reads the target branch itself.
    assert risk == "risk --format github --fail-above-percentile 95"


def test_ungated_risk_reports_without_a_percentile(tmp_path, fake_repowise) -> None:
    # The CLI reads the target branch itself for --format github.
    code, _, calls = _run(tmp_path, fake_repowise, CHECKS="risk", GITHUB_BASE_REF="main")
    assert code == 0
    assert calls.strip() == "risk --format github"
    _, _, based = _run(tmp_path, fake_repowise, CHECKS="risk", BASE="v1...HEAD")
    assert based.splitlines()[-1] == "risk v1...HEAD --format github"


def test_an_unknown_check_is_refused(tmp_path, fake_repowise) -> None:
    code, _, calls = _run(tmp_path, fake_repowise, CHECKS="coverage,lint")
    assert code == 2
    assert calls == ""


def test_a_yaml_block_list_of_checks_runs_each_gate(tmp_path, fake_repowise) -> None:
    code, outputs, _ = _run(tmp_path, fake_repowise, CHECKS="doc-drift\nsecurity\n")
    assert code == 0
    assert outputs.split() == ["doc-drift=0", "security=0"]


def test_no_check_selected_is_refused(tmp_path, fake_repowise) -> None:
    code, _, calls = _run(tmp_path, fake_repowise, CHECKS=" ")
    assert code == 2
    assert calls == ""


# -- test selection -----------------------------------------------------------

SELECTOR = ROOT / "ci" / "github" / "impacted-tests.sh"


@pytest.fixture
def fake_selector(tmp_path: Path) -> Path:
    """A ``repowise`` that logs its arguments; ``update`` exits ``$FAKE_UPDATE``,
    anything else prints ``$FAKE_OUT`` and exits ``$FAKE_CODE``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "repowise"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'echo "$*" >> "$FAKE_LOG"\n'
        'if [ "$1" = update ]; then exit "${FAKE_UPDATE:-0}"; fi\n'
        'echo "why it chose" >&2\n'
        'printf "%s\\n" "${FAKE_OUT-}"\n'
        'exit "${FAKE_CODE:-0}"\n',
        encoding="utf-8",
        newline="\n",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _outputs(text: str) -> dict[str, str]:
    """``$GITHUB_OUTPUT`` parsed the way the runner does, delimiter form included."""
    out: dict[str, str] = {}
    lines = iter(text.splitlines())
    for line in lines:
        if "<<" in line:
            name, delim = line.split("<<", 1)
            out[name] = "\n".join(iter(lambda: next(lines), delim))
        else:
            name, value = line.split("=", 1)
            out[name] = value
    return out


def test_the_selection_step_reads_only_what_the_action_passes() -> None:
    step = next(s for s in _action()["runs"]["steps"] if s.get("id") == "impacted")
    read = set(re.findall(r"\$\{?([A-Z][A-Z_]+)", SELECTOR.read_text(encoding="utf-8")))
    assert read - {"GITHUB_OUTPUT", "GITHUB_BASE_REF", "RANDOM"} <= set(step["env"])
    assert set(step["env"]) - {"PYTHONUTF8"} <= read
    assert step["if"] == "inputs.impacted-tests == 'true'"
    outputs = _action()["outputs"]
    assert outputs["impacted-tests"]["value"] == "${{ steps.impacted.outputs.impacted-tests }}"
    assert outputs["run-all-tests"]["value"] == "${{ steps.impacted.outputs.run-all-tests }}"


def _select(
    tmp_path: Path, bin_dir: Path, *, cached: bool = False, **env: str
) -> tuple[int, dict[str, str], list[str]]:
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not available")
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    if cached:
        (repo / ".repowise").mkdir(exist_ok=True)
        (repo / ".repowise" / "state.json").write_text("{}", encoding="utf-8")
    out, log = tmp_path / "out", tmp_path / "log"
    full = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(out),
        "GITHUB_BASE_REF": "",
        "FAKE_LOG": str(log),
        "BASE": "",
        "RUNNER": "auto",
        **env,
    }
    proc = subprocess.run([bash, str(SELECTOR)], cwd=repo, env=full, capture_output=True, text=True)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return proc.returncode, _outputs(out.read_text(encoding="utf-8")), calls


def test_the_selection_step_passes_the_subset_on(tmp_path, fake_selector) -> None:
    code, outputs, calls = _select(
        tmp_path,
        fake_selector,
        BASE="origin/main...HEAD",
        RUNNER="pytest",
        FAKE_OUT="tests/test_a.py 'tests/test_b.py::test_x[a b]'",
    )
    assert code == 0
    assert calls == ["impacted-tests origin/main...HEAD --format args --runner pytest"]
    assert outputs == {
        "impacted-tests": "tests/test_a.py 'tests/test_b.py::test_x[a b]'",
        "run-all-tests": "false",
    }


def test_the_selection_step_flags_a_full_run(tmp_path, fake_selector) -> None:
    code, outputs, calls = _select(tmp_path, fake_selector, FAKE_OUT=":all")
    assert code == 0
    # Without a base the CLI reads the pull request's target itself.
    assert calls == ["impacted-tests --format args --runner auto"]
    assert outputs == {"impacted-tests": ":all", "run-all-tests": "true"}


@pytest.mark.parametrize(
    "env",
    [
        {"FAKE_OUT": "", "FAKE_CODE": "2"},  # could not evaluate
        {"FAKE_OUT": "tests/test_a.py\nrun-all-tests=false"},  # not one line
    ],
)
def test_a_selection_that_cannot_be_trusted_runs_every_test(tmp_path, fake_selector, env) -> None:
    code, outputs, _ = _select(tmp_path, fake_selector, **env)
    assert code == 0
    assert outputs == {"impacted-tests": ":all", "run-all-tests": "true"}


def test_the_selection_step_updates_a_cached_index_first(tmp_path, fake_selector) -> None:
    code, outputs, calls = _select(tmp_path, fake_selector, cached=True, FAKE_OUT="t.py")
    assert code == 0
    assert calls == ["update --index-only", "impacted-tests --format args --runner auto"]
    assert outputs["run-all-tests"] == "false"
    failed, outputs, calls = _select(tmp_path, fake_selector, cached=True, FAKE_UPDATE="1")
    assert failed == 0
    assert calls[-1] == "update --index-only"
    assert outputs == {"impacted-tests": ":all", "run-all-tests": "true"}


def _gitlab_select(tmp_path, bin_dir, *, cached: bool = False, **env) -> tuple[int, str, str]:
    if cached:
        (tmp_path / ".repowise").mkdir(exist_ok=True)
        (tmp_path / ".repowise" / "state.json").write_text("{}", encoding="utf-8")
    code = _run_gitlab_job(
        tmp_path, bin_dir, "repowise-impacted-tests", REPOWISE_IMPACTED_TESTS_RUNNER="go", **env
    )
    report = (tmp_path / "repowise-impacted-tests.env").read_text(encoding="utf-8")
    return code, report, (tmp_path / "impacted-tests.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("out", "exit_code", "args", "run_all"),
    [
        ("./pkg/x", "0", "./pkg/x\n", "false"),
        (":all", "0", ":all\n", "true"),
        ("", "2", ":all\n", "true"),  # could not evaluate
        ("./a\n./b", "0", ":all\n", "true"),  # not one line
    ],
)
def test_the_gitlab_selection_job_fails_closed(
    tmp_path, fake_selector, out, exit_code, args, run_all
) -> None:
    job = _gitlab_jobs()["repowise-impacted-tests"]
    assert "allow_failure" not in job  # it never fails: a failure means run everything
    assert job["artifacts"]["reports"]["dotenv"] == "repowise-impacted-tests.env"
    assert job["artifacts"]["paths"] == ["impacted-tests.txt"]
    assert "$REPOWISE_IMPACTED_TESTS" in job["rules"][0]["if"]
    code, report, written = _gitlab_select(
        tmp_path, fake_selector, FAKE_OUT=out, FAKE_CODE=exit_code
    )
    assert code == 0
    assert (tmp_path / "log").read_text(encoding="utf-8").strip() == (
        "impacted-tests origin/main...HEAD --format args --runner go"
    )
    # Only the flag travels as a variable; the arguments are an artifact.
    assert report == f"RUN_ALL_TESTS={run_all}\n"
    assert written == args


def test_the_gitlab_job_runs_everything_when_the_cache_cannot_update(
    tmp_path, fake_selector
) -> None:
    code, report, written = _gitlab_select(tmp_path, fake_selector, cached=True, FAKE_UPDATE="1")
    assert code == 0
    assert report == "RUN_ALL_TESTS=true\n" and written == ":all\n"
    assert (tmp_path / "log").read_text(encoding="utf-8").strip() == "update --index-only"
