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


def _doctor_with(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    pinned: str,
    stale: tuple[str, ...] = (),
    matcher: str | None = None,
    command: str = "repowise",
):
    """Doctor with one unlaunchable required project entry and the rest controlled."""
    import shutil

    from repowise.cli import mcp_config
    from repowise.cli.agent_adapters.codex import SHELL_TOOL_MATCHER
    from repowise.cli.editor_integrations import codex_config

    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    monkeypatch.setattr(mcp_config, "resolve_repowise_command", lambda: pinned)
    monkeypatch.setattr(codex_target, "stale_prompts", lambda: list(stale))
    monkeypatch.setattr(
        codex_config, "codex_rewrite_hook_matcher", lambda: matcher or SHELL_TOOL_MATCHER
    )
    monkeypatch.setattr(codex_config, "codex_supports_rewrite", lambda: True)
    _write_required(codex_target.project_config_path(tmp_path), command)
    return codex_target.TARGET.doctor(repo_path=tmp_path)


def test_the_install_owns_the_fix_slot_when_the_server_is_the_only_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _doctor_with(tmp_path, monkeypatch, pinned="repowise")

    assert report.fix_command == "pip install repowise"


def test_stale_prompts_keep_their_fix_when_the_install_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _doctor_with(tmp_path, monkeypatch, pinned="repowise", stale=("a.md",))

    assert report.fix_command == "repowise agents add --target=codex"
    # The install is still named, in the server's own issue.
    assert "pip install repowise" in report.issues[0]


def test_an_unlaunchable_server_alone_leaves_the_fix_in_its_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _doctor_with(tmp_path, monkeypatch, pinned="/opt/rw/bin/repowise")

    assert len(report.issues) == 1
    assert report.fix_command is None
    assert report.repairable is False
    # A committed project file gets PATH advice, never a machine's absolute path.
    assert "put /opt/rw/bin on PATH" in report.issues[0].replace("\\", "/")
    assert 'set command = "' not in report.issues[0]


def test_stale_prompts_keep_their_fix_beside_an_unlaunchable_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _doctor_with(tmp_path, monkeypatch, pinned="/opt/rw/bin/repowise", stale=("a.md",))

    assert report.issues[0].startswith("Codex will not start:")
    assert report.fix_command == "repowise agents add --target=codex"


def test_a_stale_hook_stays_repairable_beside_an_unlaunchable_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _doctor_with(tmp_path, monkeypatch, pinned="/opt/rw/bin/repowise", matcher="shell")

    assert report.fix_command == "repowise hook rewrite install"
    assert report.repairable is True


def test_a_missing_absolute_command_is_told_to_reset_its_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _doctor_with(
        tmp_path, monkeypatch, pinned="repowise", command="/old/venv/bin/repowise"
    )

    issue = report.issues[0]
    assert "pip install repowise" in issue
    assert 'set command = "repowise" in that table' in issue


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


def test_a_project_entry_with_a_missing_absolute_command_goes_back_to_the_bare_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _doctor_with(
        tmp_path, monkeypatch, pinned="/opt/rw/bin/repowise", command=r"C:\old\repowise.exe"
    )

    issue = report.issues[0].replace("\\", "/")
    assert "put /opt/rw/bin on PATH" in issue
    assert 'set command = "repowise" in that table' in issue


def test_a_missing_hook_keeps_its_install_command_beside_an_unlaunchable_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.cli.editor_integrations import codex_config

    _doctor_with(tmp_path, monkeypatch, pinned="/opt/rw/bin/repowise")
    monkeypatch.setattr(codex_config, "codex_rewrite_hook_matcher", lambda: None)
    report = codex_target.TARGET.doctor(repo_path=tmp_path)

    assert report.issues[-1] == "The distill rewrite hook is not installed for Codex."
    assert report.fix_command == "repowise hook rewrite install"
    assert report.repairable is False


def test_a_damaged_hooks_file_still_reports_an_unlaunchable_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hooks = codex_target.user_hooks_path()
    hooks.parent.mkdir(parents=True, exist_ok=True)
    hooks.write_text("{not json", encoding="utf-8")

    report = _doctor_with(tmp_path, monkeypatch, pinned="/opt/rw/bin/repowise")

    assert report.status.value == "broken"
    assert report.issues[0].startswith("Codex will not start:")
    assert "is not valid JSON" in report.issues[1]
