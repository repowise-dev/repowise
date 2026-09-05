"""The post-commit auto-sync hook is on by default and undone with one command.

``init --yes`` and non-interactive runs used to skip the hook offer with no
message and no opt-out flag, so the paths the product recommends left the
index stale after the first commit. The offer now installs on those paths
and prints how to undo it; ``--no-hook`` skips; ``--no-editor-setup`` keeps
it off because a git hook is a write outside ``.repowise/``; an interactive
run still asks.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click
import pytest

from repowise.cli import hooks
from repowise.cli.commands.init_cmd._interactive import offer_hook_install


class _Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *args, **_kwargs) -> None:
        self.lines.append(" ".join(str(a) for a in args))

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.fixture
def editor_setup_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPOWISE_SKIP_EDITOR_SETUP", raising=False)


def test_yes_installs_and_names_the_undo(repo: Path, editor_setup_on) -> None:
    console = _Console()
    offer_hook_install(console, [repo], yes=True)
    assert hooks.status(repo) == "installed"
    assert "repowise hook uninstall" in console.text


def test_non_tty_installs_and_names_the_undo(repo: Path, editor_setup_on, monkeypatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    console = _Console()
    offer_hook_install(console, [repo])
    assert hooks.status(repo) == "installed"
    assert "repowise hook uninstall" in console.text


def test_no_hook_skips_and_says_how_to_install(repo: Path, editor_setup_on) -> None:
    console = _Console()
    offer_hook_install(console, [repo], flag=False, yes=True)
    assert hooks.status(repo) == "not installed"
    assert "repowise hook install" in console.text


def test_editor_setup_off_keeps_the_hook_off(repo: Path, monkeypatch) -> None:
    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    quiet = _Console()
    offer_hook_install(quiet, [repo], yes=True)
    assert hooks.status(repo) == "not installed"
    assert quiet.lines == []

    told = _Console()
    offer_hook_install(told, [repo], flag=True, yes=True)
    assert hooks.status(repo) == "not installed"
    assert "editor setup is off" in told.text


def test_no_editor_setup_flag_keeps_the_hook_off(repo: Path, editor_setup_on) -> None:
    console = _Console()
    offer_hook_install(console, [repo], flag=True, yes=True, no_editor_setup=True)
    assert hooks.status(repo) == "not installed"


def test_interactive_run_still_asks(repo: Path, editor_setup_on, monkeypatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(click, "confirm", lambda *_a, **_k: False)
    console = _Console()
    offer_hook_install(console, [repo])
    assert hooks.status(repo) == "not installed"

    monkeypatch.setattr(click, "confirm", lambda *_a, **_k: True)
    offer_hook_install(console, [repo])
    assert hooks.status(repo) == "installed"


def test_workspace_applies_the_default_to_every_repo(tmp_path: Path, editor_setup_on) -> None:
    repos = []
    for name in ("a", "b"):
        rp = tmp_path / name
        rp.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=rp, check=True)
        repos.append(rp)
    console = _Console()
    offer_hook_install(console, repos, ["a", "b"], yes=True)
    assert all(hooks.status(rp) == "installed" for rp in repos)
    assert "repowise hook uninstall --workspace" in console.text


def test_a_failed_install_is_reported_and_does_not_raise(
    repo: Path, editor_setup_on, monkeypatch
) -> None:
    def _boom(_p: Path) -> str:
        raise OSError("read-only hooks dir")

    monkeypatch.setattr(hooks, "install", _boom)
    console = _Console()
    offer_hook_install(console, [repo], yes=True)
    assert "not installed" in console.text
    assert "repowise hook uninstall" not in console.text


def test_uninstall_removes_what_the_default_installed(repo: Path, editor_setup_on) -> None:
    offer_hook_install(_Console(), [repo], yes=True)
    assert hooks.uninstall(repo) == "removed"
    assert hooks.status(repo) == "not installed"
