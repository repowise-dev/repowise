"""The repo ``.mcp.json`` entry is left out when a user-scope repowise server serves the repo.

Claude Code reports a project entry and a user entry with the same name as
conflicting scopes, so init and refresh write ``.mcp.json`` only when neither
the ``~/.claude.json`` entry nor the plugin serves the repo. Every test runs
against a fake home.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest
from rich.console import Console

from repowise.cli.agent_targets.formats.json_merge import merge_json_object_member
from repowise.cli.agent_targets.targets import claude_code
from repowise.cli.agent_targets.types import FileAction, InstallOptions, Scope
from repowise.cli.editor_integrations import claude_config
from repowise.cli.editor_integrations.claude import ClaudeCodeSetup
from repowise.cli.editor_setup import EditorSetupOptions

_REAL_HOME = Path.home()
_LAUNCH_DIR = {"command": "repowise", "args": ["mcp", "--transport", "stdio"]}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake))
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("USERPROFILE", str(fake))
    monkeypatch.setenv("REPOWISE_WORKSPACE_CEILING", str(tmp_path))
    assert Path.home() != _REAL_HOME
    return fake


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


def _user_entry(home: Path, entry: dict) -> None:
    (home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"repowise": entry}}), encoding="utf-8"
    )


def _plugin(home: Path, *, enabled: bool | None = None) -> None:
    manifest = home / ".claude" / "plugins" / "installed_plugins.json"
    manifest.parent.mkdir(parents=True)
    install = {"scope": "user", "installPath": str(home), "version": "0.60.0"}
    manifest.write_text(
        json.dumps({"version": 2, "plugins": {claude_code.PLUGIN_KEY: [install]}}),
        encoding="utf-8",
    )
    if enabled is not None:
        settings = home / ".claude" / "settings.json"
        settings.write_text(
            json.dumps({"enabledPlugins": {claude_code.PLUGIN_KEY: enabled}}), encoding="utf-8"
        )


def _project_entry(repo: Path) -> dict:
    return {"command": "repowise", "args": ["mcp", str(repo), "--transport", "stdio"]}


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)


# ---------------------------------------------------------------------------
# When the user scope serves the repo
# ---------------------------------------------------------------------------


def test_no_user_entry_writes_project_entry(home: Path, repo: Path) -> None:
    result = claude_code.sync_project_mcp_config(repo)

    assert result.action is FileAction.CREATED
    assert "repowise" in json.loads((repo / ".mcp.json").read_text())["mcpServers"]


def test_launch_dir_user_entry_skips_project_entry(home: Path, repo: Path) -> None:
    _user_entry(home, _LAUNCH_DIR)

    result = claude_code.sync_project_mcp_config(repo)

    assert result.action is FileAction.NOT_FOUND
    assert not (repo / ".mcp.json").exists()


def test_user_entry_naming_this_repo_skips_project_entry(home: Path, repo: Path) -> None:
    _user_entry(home, _project_entry(repo))

    assert claude_code.sync_project_mcp_config(repo).action is FileAction.NOT_FOUND


def test_user_entry_serving_another_repo_keeps_project_entry(
    home: Path, repo: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    _user_entry(home, _project_entry(other))

    assert claude_code.sync_project_mcp_config(repo).action is FileAction.CREATED


def test_user_entry_with_missing_pinned_command_keeps_project_entry(
    home: Path, repo: Path, tmp_path: Path
) -> None:
    gone = str(tmp_path / "gone-venv" / "Scripts" / "repowise.exe")
    _user_entry(home, {**_LAUNCH_DIR, "command": gone})

    assert claude_code.sync_project_mcp_config(repo).action is FileAction.CREATED


def test_enabled_plugin_skips_project_entry(home: Path, repo: Path) -> None:
    _plugin(home)

    assert claude_code.sync_project_mcp_config(repo).action is FileAction.NOT_FOUND
    assert not (repo / ".mcp.json").exists()


def test_disabled_plugin_keeps_project_entry(home: Path, repo: Path) -> None:
    _plugin(home, enabled=False)

    assert claude_code.sync_project_mcp_config(repo).action is FileAction.CREATED


# ---------------------------------------------------------------------------
# An entry an older release wrote
# ---------------------------------------------------------------------------


def test_untracked_file_holding_only_our_entry_is_deleted(home: Path, repo: Path) -> None:
    claude_code.write_project_mcp_config(repo)
    _user_entry(home, _LAUNCH_DIR)

    result = claude_code.sync_project_mcp_config(repo)

    assert result.action is FileAction.REMOVED
    assert not (repo / ".mcp.json").exists()


def test_other_servers_survive_byte_for_byte(home: Path, repo: Path) -> None:
    config = repo / ".mcp.json"
    config.write_text('{\n  "mcpServers": {\n    "other": {"command": "x"}\n  }\n}\n')
    original = config.read_text()
    merge_json_object_member(config, "mcpServers", "repowise", _project_entry(repo))
    _user_entry(home, _LAUNCH_DIR)

    assert claude_code.sync_project_mcp_config(repo).action is FileAction.REMOVED
    assert config.read_text() == original


def test_tracked_file_is_left_alone(home: Path, repo: Path) -> None:
    _git_init(repo)
    claude_code.write_project_mcp_config(repo)
    subprocess.run(["git", "add", ".mcp.json"], cwd=repo, check=True)
    before = (repo / ".mcp.json").read_bytes()
    _user_entry(home, _LAUNCH_DIR)

    result = claude_code.sync_project_mcp_config(repo)

    assert result.action is FileAction.KEPT
    assert "tracked in git" in (result.reason or "")
    assert (repo / ".mcp.json").read_bytes() == before


def test_untracked_file_in_a_git_repo_is_retired(home: Path, repo: Path) -> None:
    _git_init(repo)
    claude_code.write_project_mcp_config(repo)
    _user_entry(home, _LAUNCH_DIR)

    assert claude_code.sync_project_mcp_config(repo).action is FileAction.REMOVED
    assert not (repo / ".mcp.json").exists()


def test_unparseable_file_is_left_alone(home: Path, repo: Path) -> None:
    config = repo / ".mcp.json"
    text = '{\n  // ours\n  "mcpServers": {"repowise": {"command": "repowise"}}\n}\n'
    config.write_text(text)
    _user_entry(home, _LAUNCH_DIR)

    assert claude_code.sync_project_mcp_config(repo).action is FileAction.NOT_FOUND
    assert config.read_text() == text


def test_hand_shaped_entry_is_left_alone(home: Path, repo: Path) -> None:
    config = repo / ".mcp.json"
    config.write_text(json.dumps({"mcpServers": {"repowise": {"command": "uvx"}}}))
    before = config.read_text()
    _user_entry(home, _LAUNCH_DIR)

    assert claude_code.sync_project_mcp_config(repo).action is FileAction.KEPT
    assert config.read_text() == before


# ---------------------------------------------------------------------------
# Call sites: init's project files, agents refresh / doctor --repair, agents add
# ---------------------------------------------------------------------------


def _silent() -> Console:
    return Console(file=io.StringIO())


def test_init_project_files_leave_out_served_mcp_json(
    home: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.cli.editor_integrations import claude as claude_integration

    monkeypatch.setattr(claude_integration, "maybe_generate_claude_md", lambda *a, **k: None)
    _user_entry(home, _LAUNCH_DIR)

    written = ClaudeCodeSetup().write_project_files(_silent(), repo, EditorSetupOptions())

    assert written == []
    assert not (repo / ".mcp.json").exists()


def test_init_project_files_write_mcp_json_when_not_served(
    home: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.cli.editor_integrations import claude as claude_integration

    monkeypatch.setattr(claude_integration, "maybe_generate_claude_md", lambda *a, **k: None)

    written = ClaudeCodeSetup().write_project_files(_silent(), repo, EditorSetupOptions())

    assert written == [repo / ".mcp.json"]


def test_refresh_retires_project_entry_and_add_writes_it(home: Path, repo: Path) -> None:
    claude_code.write_project_mcp_config(repo)
    _user_entry(home, _LAUNCH_DIR)

    refresh = claude_code.TARGET.install(Scope.PROJECT, InstallOptions(), repo_path=repo)
    assert [f.action for f in refresh.files] == [FileAction.REMOVED]
    assert not (repo / ".mcp.json").exists()

    add = claude_code.TARGET.install(Scope.PROJECT, InstallOptions(retarget=True), repo_path=repo)
    assert [f.action for f in add.files] == [FileAction.CREATED]


def test_serves_reads_the_workspace_root(home: Path, tmp_path: Path) -> None:
    root = tmp_path / "ws"
    member = root / "api"
    member.mkdir(parents=True)
    (root / ".repowise-workspace.yaml").write_text("repos: []\n")
    _user_entry(home, _project_entry(root))

    assert claude_config.claude_code_user_scope_serves(member)


def test_doctor_registration_row_is_clean_without_project_entry(home: Path, repo: Path) -> None:
    from repowise.cli.commands.doctor_cmd._checks.integrations import _claude_registration_check

    _user_entry(home, _LAUNCH_DIR)
    claude_code.sync_project_mcp_config(repo)

    row, wedged = _claude_registration_check(repo)

    assert not wedged
    assert row.ok and row.level == "ok"
    assert row.detail == "serves the repo Claude Code is started in"


# ---------------------------------------------------------------------------
# The ~/.claude.json insert itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("trailing", ["", "\n"])
def test_container_inserted_at_root_member_indent(tmp_path: Path, trailing: str) -> None:
    config = tmp_path / ".claude.json"
    config.write_bytes(('{\n  "numStartups": 3\n}' + trailing).encode())

    merge_json_object_member(config, "mcpServers", "repowise", {"command": "repowise"})

    assert config.read_bytes().decode() == (
        '{\n  "numStartups": 3,\n  "mcpServers": {\n    "repowise": {\n'
        '      "command": "repowise"\n    }\n  }\n}' + trailing
    )


def test_refresh_with_plugin_retires_entry_without_the_both_note(home: Path, repo: Path) -> None:
    from repowise.cli.commands.agents_cmd import _write_payload

    claude_code.write_project_mcp_config(repo)
    _plugin(home, enabled=True)

    payload = _write_payload(
        "refresh", repo, [claude_code.TARGET], "project", remove=False, refresh_only=True
    )

    project = payload["agents"][0]["writes"]["project"]
    assert [f["action"] for f in project["files"]] == ["removed"]
    assert not (repo / ".mcp.json").exists()
    assert not any("load repowise from both" in note for note in project["notes"])


def test_init_project_files_leave_out_a_kept_mcp_json(
    home: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.cli.editor_integrations import claude as claude_integration

    monkeypatch.setattr(claude_integration, "maybe_generate_claude_md", lambda *a, **k: None)
    config = repo / ".mcp.json"
    config.write_text(json.dumps({"mcpServers": {"repowise": {"command": "uvx"}}}))
    before = config.read_text()
    _user_entry(home, _LAUNCH_DIR)

    written = ClaudeCodeSetup().write_project_files(_silent(), repo, EditorSetupOptions())

    assert written == []
    assert config.read_text() == before
