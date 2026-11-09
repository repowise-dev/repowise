"""The git post-commit hook runs the install that wrote it before falling back to PATH."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from repowise.cli import hooks


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


def _fake_install(tmp_path: Path, name: str) -> Path:
    """A ``repowise`` script that records the subcommand it was run with."""
    script = tmp_path / name / "bin" / "repowise"
    script.parent.mkdir(parents=True)
    script.write_text('#!/bin/sh\necho "$@" > "$PWD/.repowise/ran"\n', encoding="utf-8", newline="\n")
    script.chmod(0o755)
    return script


def _pin(monkeypatch: pytest.MonkeyPatch, script: Path | str) -> str:
    pinned = str(script).replace("\\", "/")
    monkeypatch.setattr(hooks, "_pinned_repowise", lambda: pinned)
    return pinned


def _hook(repo: Path) -> str:
    return (repo / ".git" / "hooks" / "post-commit").read_text(encoding="utf-8")


def test_install_pins_the_running_install_before_the_path_lookup(repo, tmp_path, monkeypatch):
    pinned = _pin(monkeypatch, _fake_install(tmp_path, "a"))
    hooks.install(repo)
    body = _hook(repo)
    assert body.index(f"if [ -x '{pinned}' ]; then") < body.index("elif command -v repowise")
    assert f"'{pinned}' update >> \"$LOG\" 2>&1" in body
    assert hooks.install(repo) == "already installed"


def test_unpinnable_install_writes_the_path_lookup_body(repo, monkeypatch):
    _pin(monkeypatch, "repowise")
    hooks.install(repo)
    assert hooks._HOOK_SCRIPT.rstrip() in _hook(repo)


def test_quote_in_the_install_path_round_trips(monkeypatch):
    pinned = _pin(monkeypatch, "/opt/it's here/bin/repowise")
    assert hooks._block_pin(hooks._hook_script()) == pinned


def test_heal_repins_an_unpinned_hook_and_keeps_other_content(repo, tmp_path, monkeypatch):
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho mine\n" + hooks._HOOK_SCRIPT + "echo after\n", encoding="utf-8")
    pinned = _pin(monkeypatch, _fake_install(tmp_path, "a"))
    assert hooks.heal(repo) is True
    body = _hook(repo)
    assert f"if [ -x '{pinned}' ]" in body
    assert body.startswith("#!/bin/sh\necho mine\n") and body.endswith("echo after\n")
    assert hooks.heal(repo) is False


def test_heal_leaves_a_hook_pinned_to_another_live_install(repo, tmp_path, monkeypatch):
    other = _pin(monkeypatch, _fake_install(tmp_path, "other"))
    hooks.install(repo)
    _pin(monkeypatch, _fake_install(tmp_path, "mine"))
    assert hooks.heal(repo) is False
    assert f"'{other}'" in _hook(repo)
    # install is the explicit repin.
    assert hooks.install(repo) == "upgraded"


def test_heal_repins_a_hook_whose_install_is_gone(repo, tmp_path, monkeypatch):
    gone = _fake_install(tmp_path, "gone")
    _pin(monkeypatch, gone)
    hooks.install(repo)
    gone.unlink()
    pinned = _pin(monkeypatch, _fake_install(tmp_path, "new"))
    assert hooks.heal(repo) is True
    assert f"'{pinned}'" in _hook(repo) and "gone" not in _hook(repo)


def test_heal_without_a_hook_is_a_no_op(repo):
    assert hooks.heal(repo) is False
    assert not (repo / ".git" / "hooks" / "post-commit").exists()


def test_the_hook_runs_the_pinned_install_when_path_has_none(repo, tmp_path, monkeypatch):
    sh = shutil.which("sh")
    git = shutil.which("git")
    if sh is None or git is None or Path(sh).parent.name.lower() == "system32":
        pytest.skip("no POSIX sh to run the hook under")
    _pin(monkeypatch, _fake_install(tmp_path, "a"))
    hooks.install(repo)
    (repo / ".repowise").mkdir()
    (repo / ".repowise" / "state.json").write_text("{}", encoding="utf-8")
    # Only sh and git reachable: a PATH lookup alone would find no repowise.
    path = os.pathsep.join(dict.fromkeys([str(Path(sh).parent), str(Path(git).parent)]))
    env = {"PATH": path, "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
    subprocess.run([sh, str(repo / ".git" / "hooks" / "post-commit")], cwd=repo, env=env, check=True, timeout=30)
    ran = repo / ".repowise" / "ran"
    deadline = time.monotonic() + 20
    while not ran.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert ran.read_text(encoding="utf-8").strip() == "update"
