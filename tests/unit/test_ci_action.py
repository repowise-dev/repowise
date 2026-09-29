"""The GitHub Action, its runner script and the CI guide stay in step.

``action.yml`` passes inputs to ``ci/github/run-gates.sh`` as environment
variables and the guide documents every input, so a renamed input that one of
the three misses would fail silently in a user's workflow. The runner is also
run here against a stand-in ``repowise`` to pin its exit-code rules.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
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


@pytest.fixture
def fake_repowise(tmp_path: Path) -> Path:
    """A ``repowise`` that logs its arguments and exits with ``$FAKE_<GATE>``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "repowise"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'echo "$*" >> "$FAKE_LOG"\n'
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
