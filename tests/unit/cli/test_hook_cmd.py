"""Workspace behavior for the post-commit hook install/uninstall commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands import hook_cmd
from repowise.cli.commands.hook_cmd import hook_group


class _Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *args, **_kwargs) -> None:
        self.lines.append(" ".join(str(arg) for arg in args))

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    for name in ("backend", "frontend"):
        repo = tmp_path / name
        repo.mkdir()
        (repo / ".repowise").mkdir()
    (tmp_path / "unindexed").mkdir()
    (tmp_path / ".repowise-workspace.yaml").write_text(
        "version: 1\n"
        "default_repo: backend\n"
        "repos:\n"
        "  - path: backend\n"
        "    alias: backend\n"
        "    is_primary: true\n"
        "  - path: frontend\n"
        "    alias: frontend\n"
        "  - path: unindexed\n"
        "    alias: unindexed\n"
    )
    return tmp_path


def test_install_skips_unindexed_repos_and_colours_failures(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []

    def _install(repo_path: Path) -> str:
        calls.append(repo_path)
        if repo_path.name == "backend":
            return "installed"
        return "not a git repository"

    console = _Console()
    monkeypatch.setattr(hook_cmd, "console", console)
    monkeypatch.setattr("repowise.cli.hooks.install", _install)

    result = CliRunner().invoke(hook_group, ["install", str(workspace)])

    assert result.exit_code == 0
    assert calls == [workspace / "backend", workspace / "frontend"]
    assert "  backend: [green]installed[/green]" in console.text
    assert "  frontend: [yellow]not a git repository[/yellow]" in console.text
    assert "  unindexed:" not in console.text


def test_install_exits_nonzero_when_no_indexed_target_succeeds(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    console = _Console()
    monkeypatch.setattr(hook_cmd, "console", console)
    monkeypatch.setattr("repowise.cli.hooks.install", lambda _repo_path: "not a git repository")

    result = CliRunner().invoke(hook_group, ["install", str(workspace)])

    assert result.exit_code != 0
    assert "No post-commit hooks were installed." in result.output
    assert "[green]" not in console.text


def test_uninstall_uses_the_same_indexed_targets_and_failure_colour(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []

    def _uninstall(repo_path: Path) -> str:
        calls.append(repo_path)
        if repo_path.name == "backend":
            return "removed"
        return "not a git repository"

    console = _Console()
    monkeypatch.setattr(hook_cmd, "console", console)
    monkeypatch.setattr("repowise.cli.hooks.uninstall", _uninstall)

    result = CliRunner().invoke(hook_group, ["uninstall", str(workspace)])

    assert result.exit_code == 0
    assert calls == [workspace / "backend", workspace / "frontend"]
    assert "  backend: [green]removed[/green]" in console.text
    assert "  frontend: [yellow]not a git repository[/yellow]" in console.text
    assert "  unindexed:" not in console.text


def test_uninstall_exits_nonzero_when_no_indexed_target_succeeds(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hook_cmd, "console", _Console())
    monkeypatch.setattr("repowise.cli.hooks.uninstall", lambda _repo_path: "not a git repository")

    result = CliRunner().invoke(hook_group, ["uninstall", str(workspace)])

    assert result.exit_code != 0
    assert "No post-commit hooks were uninstalled." in result.output
