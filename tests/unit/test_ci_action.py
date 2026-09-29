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
    assert {"repowise-coverage", "repowise-doc-drift", "repowise-security"} <= set(jobs)


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
        "DOC_DRIFT_BASELINE": "",
        "SECURITY_FAIL_ON": "high",
        "SECURITY_BASELINE": "",
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
        COVERAGE_REPORT="a.info\nb.xml",
        COVERAGE_FAIL_UNDER="80",
        SECURITY_BASELINE=".security-baseline.json",
    )
    assert code == 0
    coverage, security = calls.splitlines()
    assert coverage == (
        "coverage check origin/main...HEAD --format github "
        "--report a.info --report b.xml --fail-under 80"
    )
    assert security == (
        "security check origin/main...HEAD --fail-on high "
        "--baseline .security-baseline.json --format github"
    )


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
