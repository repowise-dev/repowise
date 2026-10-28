"""A single-repo init next to other git repos names the workspace command."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from repowise.cli.commands.init_cmd import _sibling_hint, command, init_command
from repowise.core.workspace import WORKSPACE_CONFIG_FILENAME


def _repo(path: Path) -> Path:
    (path / ".git").mkdir(parents=True)
    return path


@pytest.fixture
def out(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    buf = io.StringIO()
    monkeypatch.setattr(_sibling_hint, "console", Console(file=buf, width=400))
    return buf


def test_counts_the_repos_beside_this_one(tmp_path: Path) -> None:
    backend = _repo(tmp_path / "backend")
    _repo(tmp_path / "frontend")
    assert _sibling_hint.sibling_repo_count(backend) == 2


def test_a_repo_alone_in_its_folder_gets_no_hint(tmp_path: Path, out: io.StringIO) -> None:
    backend = _repo(tmp_path / "backend")
    (tmp_path / "notes").mkdir()
    _sibling_hint.suggest_workspace(backend)
    assert out.getvalue() == ""


def test_a_workspace_member_gets_no_hint(tmp_path: Path) -> None:
    backend = _repo(tmp_path / "backend")
    _repo(tmp_path / "frontend")
    (tmp_path / WORKSPACE_CONFIG_FILENAME).write_text("repos: []\n", encoding="utf-8")
    assert _sibling_hint.sibling_repo_count(backend) == 0


def test_repos_in_the_home_folder_get_no_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _repo(tmp_path / "backend")
    _repo(tmp_path / "frontend")
    monkeypatch.setattr(_sibling_hint.Path, "home", lambda: tmp_path)
    assert _sibling_hint.sibling_repo_count(backend) == 0


def test_the_hint_names_the_parent_folder(tmp_path: Path, out: io.StringIO) -> None:
    backend = _repo(tmp_path / "backend")
    _repo(tmp_path / "frontend")
    _sibling_hint.suggest_workspace(backend)
    text = out.getvalue()
    assert "holds 2 git repos" in text
    assert f'repowise init "{tmp_path}"' in text


@pytest.mark.parametrize(("flags", "hinted"), [((), True), (("--no-workspace",), False)])
def test_init_prints_the_hint_before_the_single_repo_run(
    tmp_path: Path,
    out: io.StringIO,
    monkeypatch: pytest.MonkeyPatch,
    flags: tuple[str, ...],
    hinted: bool,
) -> None:
    backend = _repo(tmp_path / "backend")
    _repo(tmp_path / "frontend")
    ran: list[Path] = []
    monkeypatch.setattr(command, "_run_single_repo", lambda _o, path, _s: ran.append(path))
    result = CliRunner().invoke(
        init_command, [str(backend), "--yes", "--no-editor-setup", *flags]
    )
    assert result.exit_code == 0, result.output
    assert ran == [backend.resolve()]
    assert ("repowise init" in out.getvalue()) is hinted
