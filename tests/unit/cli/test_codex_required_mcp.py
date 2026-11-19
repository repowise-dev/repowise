"""Codex waits for the repowise server, and ``doctor`` says when that would stop Codex.

Home is redirected by the CLI conftest, so nothing here reaches real user config.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from repowise.cli.agent_targets.targets import codex as codex_target
from repowise.cli.agent_targets.types import Scope


def _table(repo: Path) -> dict:
    text = (repo / ".codex" / "config.toml").read_text(encoding="utf-8")
    return tomllib.loads(text)["mcp_servers"]["repowise"]


def test_the_entry_makes_codex_wait_for_the_server(tmp_path: Path) -> None:
    codex_target.TARGET.install(Scope.PROJECT, repo_path=tmp_path)

    table = _table(tmp_path)
    assert table["required"] is True
    assert table["startup_timeout_sec"] == codex_target.STARTUP_TIMEOUT_SEC


def test_print_config_shows_the_same_entry(tmp_path: Path) -> None:
    printed = codex_target.TARGET.print_config(Scope.PROJECT, repo_path=tmp_path)
    assert "required = true" in printed
    assert f"startup_timeout_sec = {codex_target.STARTUP_TIMEOUT_SEC}" in printed


def test_agents_add_refreshes_an_entry_an_older_version_wrote(tmp_path: Path) -> None:
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(
        "[mcp_servers.repowise]\n"
        'command = "repowise"\n'
        'args = ["mcp"]\n'
        f'cwd = "{tmp_path.as_posix()}"\n'
        "startup_timeout_sec = 20\n"
        'env_key = "mine"\n',
        encoding="utf-8",
    )

    codex_target.TARGET.install(Scope.PROJECT, repo_path=tmp_path)

    table = _table(tmp_path)
    assert table["required"] is True
    assert table["startup_timeout_sec"] == codex_target.STARTUP_TIMEOUT_SEC
    assert table["env_key"] == "mine"


def _write_required(path: Path, command: str, *, required: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[mcp_servers.repowise]\n"
        f"command = {command!r}\n"
        'args = ["mcp"]\n'
        f"required = {'true' if required else 'false'}\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("scope", ["user", "project"])
def test_doctor_warns_when_a_required_server_cannot_launch(
    scope: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    path = (
        codex_target.user_hooks_path().parent / "config.toml"
        if scope == "user"
        else codex_target.project_config_path(tmp_path)
    )
    _write_required(path, "repowise")

    report = codex_target.TARGET.doctor(repo_path=tmp_path)

    assert report.status.value == "stale"
    issue = report.issues[0]
    assert issue.startswith("Codex will not start: [mcp_servers.repowise]")
    assert str(path) in issue
    assert "To fix," in issue


def test_doctor_is_quiet_when_the_command_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda cmd: f"/bin/{cmd}")
    _write_required(codex_target.project_config_path(tmp_path), "repowise")

    assert codex_target.unlaunchable_required_servers(tmp_path) == []


def test_doctor_ignores_an_entry_that_is_not_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    _write_required(codex_target.project_config_path(tmp_path), "repowise", required=False)

    assert codex_target.unlaunchable_required_servers(tmp_path) == []


@pytest.mark.parametrize(("pinned", "expected_fix"), [("repowise", "pip install repowise"), ("/opt/rw/bin/repowise", None)])
def test_the_unlaunchable_server_owns_the_fix_slot(
    pinned: str, expected_fix: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    from repowise.cli import mcp_config

    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    monkeypatch.setattr(mcp_config, "resolve_repowise_command", lambda: pinned)
    _write_required(codex_target.project_config_path(tmp_path), "repowise")

    report = codex_target.TARGET.doctor(repo_path=tmp_path)

    assert report.fix_command == expected_fix
    assert report.repairable is False
    if expected_fix is None:
        # A committed project file gets PATH advice, never a machine's absolute path.
        assert "put /opt/rw/bin on PATH" in report.issues[0].replace("\\", "/")
        assert 'set command = "' not in report.issues[0]


def test_a_user_file_may_pin_the_absolute_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    from repowise.cli import mcp_config

    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    monkeypatch.setattr(mcp_config, "resolve_repowise_command", lambda: "/opt/rw/bin/repowise")
    _write_required(codex_target.user_hooks_path().parent / "config.toml", "repowise")

    (issue,) = codex_target.unlaunchable_required_servers(tmp_path)
    assert 'set command = "/opt/rw/bin/repowise"' in issue


def test_a_string_required_or_a_url_entry_is_not_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    path = codex_target.project_config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        '[mcp_servers.repowise]\ncommand = "repowise"\nargs = ["mcp"]\nrequired = "true"\n\n'
        '[mcp_servers.repowise-hosted]\nurl = "https://example.test/mcp"\nrequired = true\n',
        encoding="utf-8",
    )
    assert codex_target.unlaunchable_required_servers(tmp_path) == []
