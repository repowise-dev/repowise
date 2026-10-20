"""``repowise hook install --security``: the opt-in pre-commit security block.

It shares the post-commit hook's marker-block contract: it coexists with a
user's own pre-commit script, uninstall removes only its block, and status
reports it. Plain ``hook install`` must not add it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli import hooks
from repowise.cli.commands.hook_cmd import hook_group


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def _pre_commit(repo: Path) -> Path:
    return repo / ".git" / "hooks" / "pre-commit"


def test_install_writes_a_new_hook_and_status_reports_it(repo: Path) -> None:
    assert hooks.security_status(repo) == "not installed"
    assert hooks.install_security(repo) == "installed"
    assert b"\r" not in _pre_commit(repo).read_bytes()  # sh cannot run a CRLF script
    text = _pre_commit(repo).read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n# repowise-security-hook-start")
    assert "security check --staged" in text
    assert hooks.security_status(repo) == "installed"
    assert hooks.install_security(repo) == "already installed"


def test_install_keeps_a_user_script_and_goes_before_it(repo: Path) -> None:
    user = "#!/bin/bash\nnpm run lint\nexit 0\n"
    _pre_commit(repo).parent.mkdir(parents=True, exist_ok=True)
    _pre_commit(repo).write_text(user, encoding="utf-8")
    assert hooks.install_security(repo) == "installed"
    text = _pre_commit(repo).read_text(encoding="utf-8")
    assert text.startswith("#!/bin/bash\n# repowise-security-hook-start")
    assert text.index("repowise-security-hook-end") < text.index("npm run lint")

    assert hooks.uninstall_security(repo) == "removed (other hook content preserved)"
    assert _pre_commit(repo).read_text(encoding="utf-8") == user
    assert hooks.security_status(repo) == "not installed"


def test_uninstall_deletes_a_hook_that_held_only_the_block(repo: Path) -> None:
    hooks.install_security(repo)
    assert hooks.uninstall_security(repo) == "removed"
    assert not _pre_commit(repo).exists()
    assert hooks.uninstall_security(repo) == "no pre-commit hook found"


def test_an_older_block_is_upgraded_in_place(repo: Path) -> None:
    hooks.install_security(repo)
    hook = _pre_commit(repo)
    stale = hook.read_text(encoding="utf-8").replace("Blocks a commit", "Old wording")
    hook.write_text(stale + "echo after\n", encoding="utf-8")
    assert hooks.install_security(repo) == "upgraded"
    text = hook.read_text(encoding="utf-8")
    assert "Old wording" not in text and text.count("repowise-security-hook-start") == 1
    assert text.endswith("echo after\n")


def test_a_non_shell_hook_is_left_alone(repo: Path) -> None:
    _pre_commit(repo).parent.mkdir(parents=True, exist_ok=True)
    _pre_commit(repo).write_text("#!/usr/bin/env node\nconsole.log(1)\n", encoding="utf-8")
    assert hooks.install_security(repo).startswith("not installed")


def test_the_cli_installs_it_only_when_asked_and_uninstall_removes_both(repo: Path) -> None:
    runner = CliRunner()
    (repo / ".repowise").mkdir()  # hook subcommands act on indexed repositories only
    assert runner.invoke(hook_group, ["install", str(repo), "--no-workspace"]).exit_code == 0
    assert not _pre_commit(repo).exists()

    result = runner.invoke(hook_group, ["install", str(repo), "--no-workspace", "--security"])
    assert result.exit_code == 0 and "Pre-commit (security): installed" in result.output
    assert hooks.security_status(repo) == "installed"
    status = runner.invoke(hook_group, ["status", str(repo), "--no-workspace"])
    assert "pre-commit (security): installed" in status.output

    result = runner.invoke(hook_group, ["uninstall", str(repo), "--no-workspace"])
    assert result.exit_code == 0 and "Pre-commit (security): removed" in result.output
    assert hooks.status(repo) == "not installed" and hooks.security_status(repo) == "not installed"
    status = runner.invoke(hook_group, ["status", str(repo), "--no-workspace"])
    assert "pre-commit (security)" not in status.output


def _sh() -> str | None:
    found = shutil.which("sh")
    return found if found and "system32" not in found.lower() else None


@pytest.mark.skipif(_sh() is None, reason="needs a POSIX sh")
@pytest.mark.parametrize(("code", "blocked"), [(0, False), (1, True), (2, False)])
def test_the_block_stops_the_commit_on_exit_one_only(
    tmp_path: Path, code: int, blocked: bool
) -> None:
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "repowise").write_text(f"#!/bin/sh\nexit {code}\n", encoding="utf-8", newline="\n")
    (fake / "repowise").chmod(0o755)
    script = tmp_path / "hook.sh"
    script.write_text(
        "#!/bin/sh -e\n" + hooks._SECURITY_HOOK_SCRIPT + "echo reached\n",
        encoding="utf-8",
        newline="\n",
    )
    env = {"PATH": f"{fake.as_posix()}:/usr/bin:/bin"}
    proc = subprocess.run(
        [_sh(), "-e", str(script)], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert (proc.returncode != 0) is blocked
    assert ("reached" in proc.stdout) is not blocked


@pytest.mark.skipif(_sh() is None, reason="needs a POSIX sh")
def test_the_block_lets_the_commit_through_without_a_repowise_binary(tmp_path: Path) -> None:
    script = tmp_path / "hook.sh"
    script.write_text(
        "#!/bin/sh -e\n" + hooks._SECURITY_HOOK_SCRIPT + "echo reached\n",
        encoding="utf-8",
        newline="\n",
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    proc = subprocess.run(
        [_sh(), "-e", str(script)],
        cwd=tmp_path,
        env={"PATH": empty.as_posix()},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0 and "reached" in proc.stdout
    assert "repowise not found; security check skipped" in proc.stderr


def test_a_block_that_cannot_be_replaced_is_not_reported_as_upgraded(repo: Path) -> None:
    hook = _pre_commit(repo)
    hook.parent.mkdir(parents=True, exist_ok=True)
    # A start marker with no end marker: nothing can be replaced.
    broken = "#!/bin/sh\n# repowise-security-hook-start\necho mine\n"
    hook.write_text(broken, encoding="utf-8")
    assert hooks.install_security(repo) == "already installed"
    assert hook.read_text(encoding="utf-8") == broken
