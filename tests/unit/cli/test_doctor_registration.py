"""Doctor's wedged Claude Code MCP registration detection.

A leaked or stale ``mcpServers.repowise`` entry in ``~/.claude/settings.json``
(a moved repo, a deleted venv's pinned binary, a temp path) silently breaks
the MCP server in every session. The check must flag exactly those cases and
treat "not registered" / "can't check" as informational, never a failure.

The autouse ``_isolated_home`` conftest fixture points ``Path.home()`` at a
temp dir, so these tests write their own settings.json freely.
"""

from __future__ import annotations

import json
from pathlib import Path

from repowise.cli.commands.doctor_cmd.repo_checks import (
    _claude_registration_check,
    _registration_target,
)


def _write_settings(entry: dict) -> Path:
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps({"mcpServers": {"repowise": entry}}), encoding="utf-8")
    return settings_path


def _entry(target: Path | str, command: str = "repowise") -> dict:
    return {
        "command": command,
        "args": ["mcp", str(target), "--transport", "stdio"],
    }


def test_no_settings_file_is_informational() -> None:
    check, wedged = _claude_registration_check()
    assert check.ok is True
    assert wedged is False
    assert "not registered" in check.detail


def test_not_registered_is_informational() -> None:
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps({"env": {}}), encoding="utf-8")
    check, wedged = _claude_registration_check()
    assert check.ok is True
    assert wedged is False


def test_healthy_registration_passes(tmp_path: Path) -> None:
    _write_settings(_entry(tmp_path))
    check, wedged = _claude_registration_check()
    assert check.ok is True
    assert wedged is False
    assert str(tmp_path) in check.detail


def test_missing_target_path_is_wedged(tmp_path: Path) -> None:
    # The shape found in the wild: a registration left pointing at a
    # long-deleted pytest temp directory.
    _write_settings(_entry(tmp_path / "pytest-1457" / "gone0"))
    check, wedged = _claude_registration_check()
    assert check.ok is False
    assert wedged is True
    assert "registered path missing" in check.detail
    assert "--repair" in check.detail


def test_missing_pinned_command_is_wedged(tmp_path: Path) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    _write_settings(_entry(target, command=str(tmp_path / "dead-venv" / "repowise.exe")))
    check, wedged = _claude_registration_check()
    assert check.ok is False
    assert wedged is True
    assert "command not found" in check.detail


def test_bare_command_name_is_never_checked(tmp_path: Path) -> None:
    # Bare names resolve via PATH at session start; only pinned absolute
    # paths can go stale.
    target = tmp_path / "repo"
    target.mkdir()
    _write_settings(_entry(target, command="repowise"))
    check, wedged = _claude_registration_check()
    assert check.ok is True
    assert wedged is False


def test_malformed_settings_is_informational() -> None:
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text("{ not json", encoding="utf-8")
    check, wedged = _claude_registration_check()
    assert check.ok is True
    assert wedged is False


def test_registration_target_extraction(tmp_path: Path) -> None:
    assert _registration_target(_entry(tmp_path)) == str(tmp_path)
    # Unrecognized arg shapes → None, no false positives.
    assert _registration_target({"args": ["serve", str(tmp_path)]}) is None
    assert _registration_target({"args": "mcp"}) is None
    assert _registration_target({}) is None
    assert _registration_target({"args": ["mcp", "--transport", "stdio"]}) is None


def test_entry_serving_another_repo_is_advisory(tmp_path: Path) -> None:
    # Not broken state, so it must not fail the run; and doctor never
    # repoints the one global entry.
    other, repo = tmp_path / "other", tmp_path / "repo"
    other.mkdir()
    repo.mkdir()
    _write_settings(_entry(other))
    check, wedged = _claude_registration_check(repo)
    assert check.ok is True
    assert wedged is False
    assert f"serves {other}; this repo is not registered" in check.detail
    assert "repowise agents add --target=claude-code --scope=project" in check.detail


def test_entry_serving_this_repo_is_ok(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_settings(_entry(repo))
    check, _ = _claude_registration_check(repo)
    assert check.ok is True
    assert "not registered" not in check.detail


def test_project_mcp_json_covers_a_global_entry_elsewhere(tmp_path: Path) -> None:
    other, repo = tmp_path / "other", tmp_path / "repo"
    other.mkdir()
    repo.mkdir()
    _write_settings(_entry(other))
    (repo / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"repowise": _entry(repo)}}), encoding="utf-8"
    )
    check, _ = _claude_registration_check(repo)
    assert check.ok is True
    assert "not registered" not in check.detail


def test_workspace_entry_covers_its_member_repos(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    repo = workspace / "repo"
    repo.mkdir(parents=True)
    (workspace / ".repowise-workspace.yaml").write_text("repos: []\n", encoding="utf-8")
    _write_settings(_entry(workspace))
    check, _ = _claude_registration_check(repo)
    assert check.ok is True
    assert "not registered" not in check.detail


def _write_project_entry(repo: Path, target: Path | str) -> None:
    (repo / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"repowise": _entry(target)}}), encoding="utf-8"
    )


def test_project_mcp_json_copied_from_another_repo_does_not_count(tmp_path: Path) -> None:
    other, repo = tmp_path / "other", tmp_path / "repo"
    other.mkdir()
    repo.mkdir()
    _write_settings(_entry(other))
    _write_project_entry(repo, other)
    check, _ = _claude_registration_check(repo)
    assert check.ok is True
    assert "this repo is not registered" in check.detail


def test_relative_project_entry_counts(tmp_path: Path) -> None:
    other, repo = tmp_path / "other", tmp_path / "repo"
    other.mkdir()
    repo.mkdir()
    _write_settings(_entry(other))
    _write_project_entry(repo, ".")
    check, _ = _claude_registration_check(repo)
    assert "not registered" not in check.detail


def test_local_scope_entry_in_claude_json_counts(tmp_path: Path) -> None:
    other, repo = tmp_path / "other", tmp_path / "repo"
    other.mkdir()
    repo.mkdir()
    _write_settings(_entry(other))
    (Path.home() / ".claude.json").write_text(
        json.dumps({"projects": {str(repo): {"mcpServers": {"repowise": _entry(repo)}}}}),
        encoding="utf-8",
    )
    check, _ = _claude_registration_check(repo)
    assert "not registered" not in check.detail


def test_local_scope_entry_for_another_repo_does_not_count(tmp_path: Path) -> None:
    other, repo = tmp_path / "other", tmp_path / "repo"
    other.mkdir()
    repo.mkdir()
    _write_settings(_entry(other))
    (Path.home() / ".claude.json").write_text(
        json.dumps({"projects": {str(other): {"mcpServers": {"repowise": _entry(repo)}}}}),
        encoding="utf-8",
    )
    check, _ = _claude_registration_check(repo)
    assert "this repo is not registered" in check.detail
