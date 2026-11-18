"""``run_command``: one timed run of a user command with cwd, env and timeout control."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from repowise.core.command_runner import render_command, run_command
from repowise.core.procutils import pid_alive


def _python(code: str) -> str:
    return render_command((sys.executable, "-c", code))


def test_a_run_reports_exit_code_wall_time_and_output_tail() -> None:
    ticks = iter([10.0, 12.5])
    run = run_command(_python("print('hello')"), clock=lambda: next(ticks))
    assert run.ok and run.exit_code == 0 and not run.timed_out
    assert run.seconds == 2.5
    assert run.output_tail.strip() == "hello"


def test_a_failing_command_keeps_its_exit_code_and_last_lines() -> None:
    code = (
        "import sys; [print('line', i) for i in range(50)]; sys.stdout.flush(); "
        "print('boom', file=sys.stderr); sys.exit(3)"
    )
    run = run_command(_python(code), tail_lines=3)
    assert run.exit_code == 3 and not run.ok
    lines = run.output_tail.splitlines()
    assert len(lines) == 3
    assert lines[-1] == "boom"
    assert "line 0" not in run.output_tail


def test_the_command_runs_in_the_given_directory(tmp_path: Path) -> None:
    run = run_command(_python("import os; print(os.getcwd())"), cwd=tmp_path)
    assert Path(run.output_tail.strip()).resolve() == tmp_path.resolve()


def test_env_entries_override_the_inherited_environment(monkeypatch) -> None:
    monkeypatch.setenv("RW_RUNNER_KEEP", "kept")
    run = run_command(
        _python("import os; print(os.environ['RW_RUNNER_PROBE'], os.environ['RW_RUNNER_KEEP'])"),
        env={"RW_RUNNER_PROBE": "set"},
    )
    assert run.output_tail.strip() == "set kept"


def test_a_run_past_its_timeout_is_killed_and_reported() -> None:
    start = time.monotonic()
    run = run_command(_python("import time; time.sleep(60)"), timeout=0.5)
    assert run.timed_out and run.exit_code is None and not run.ok
    assert time.monotonic() - start < 30


def test_a_timeout_kills_the_children_the_command_started(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    code = (
        "import subprocess, sys, time; "
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"open({str(pid_file)!r}, 'w').write(str(p.pid)); time.sleep(60)"
    )
    run = run_command(_python(code), timeout=3)
    assert run.timed_out
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while pid_alive(child) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert pid_alive(child) is not True


def test_a_missing_working_directory_raises_os_error(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        run_command(_python("pass"), cwd=tmp_path / "missing")
