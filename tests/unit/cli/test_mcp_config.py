import json
import re
from pathlib import Path

import click
import pytest

from repowise.cli import mcp_config


def _repowise_entry(repo_path: Path) -> dict:
    return mcp_config.generate_mcp_config(repo_path)["mcpServers"]


def test_save_root_mcp_config_creates_missing_file(tmp_path: Path) -> None:
    config_path = mcp_config.save_root_mcp_config(tmp_path)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert "repowise" in saved["mcpServers"]


def test_save_root_mcp_config_merges_valid_existing_file(tmp_path: Path) -> None:
    config_path = tmp_path / ".mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {"other": {"command": "other"}},
                "custom": {"preserved": True},
            }
        ),
        encoding="utf-8",
    )

    mcp_config.save_root_mcp_config(tmp_path)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["mcpServers"]["other"] == {"command": "other"}
    assert "repowise" in saved["mcpServers"]
    assert saved["custom"] == {"preserved": True}


def test_save_root_mcp_config_rejects_invalid_existing_file(tmp_path: Path) -> None:
    config_path = tmp_path / ".mcp.json"
    original = '{\n  "mcpServers": {},\n}\n'
    config_path.write_text(original, encoding="utf-8")

    with pytest.raises(click.ClickException, match=re.escape(str(config_path))):
        mcp_config.save_root_mcp_config(tmp_path)

    assert config_path.read_text(encoding="utf-8") == original


def test_merge_mcp_entry_creates_missing_file(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.json"

    assert mcp_config._merge_mcp_entry(config_path, _repowise_entry(tmp_path))

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert "repowise" in saved["mcpServers"]


def test_merge_mcp_entry_merges_valid_existing_file(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {"existing": {"command": "existing"}},
                "permissions": {"allow": ["Bash(git status:*)"]},
            }
        ),
        encoding="utf-8",
    )

    assert mcp_config._merge_mcp_entry(config_path, _repowise_entry(tmp_path))

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["mcpServers"]["existing"] == {"command": "existing"}
    assert "repowise" in saved["mcpServers"]
    assert saved["permissions"] == {"allow": ["Bash(git status:*)"]}


def test_merge_mcp_entry_rejects_invalid_existing_file(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.json"
    original = '{\n  "permissions": {},\n}\n'
    config_path.write_text(original, encoding="utf-8")

    with pytest.raises(click.ClickException, match=re.escape(str(config_path))):
        mcp_config._merge_mcp_entry(config_path, _repowise_entry(tmp_path))

    assert config_path.read_text(encoding="utf-8") == original


def test_install_claude_code_hooks_creates_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    settings_path = mcp_config.install_claude_code_hooks()

    assert settings_path == tmp_path / ".claude" / "settings.json"
    saved = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "PreToolUse" in saved["hooks"]
    assert "PostToolUse" in saved["hooks"]


def test_install_claude_code_hooks_merges_valid_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Bash(git status:*)"]},
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Read",
                            "hooks": [{"type": "command", "command": "echo read"}],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    assert mcp_config.install_claude_code_hooks() == settings_path

    saved = json.loads(settings_path.read_text(encoding="utf-8"))
    assert saved["permissions"] == {"allow": ["Bash(git status:*)"]}
    assert saved["hooks"]["PreToolUse"][0]["matcher"] == "Read"
    assert any(
        hook["command"] == "repowise augment"
        for entry in saved["hooks"]["PreToolUse"]
        for hook in entry["hooks"]
    )
    assert "PostToolUse" in saved["hooks"]


def test_install_claude_code_hooks_rejects_invalid_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    original = '{\n  "permissions": {},\n}\n'
    settings_path.write_text(original, encoding="utf-8")

    with pytest.raises(click.ClickException, match=re.escape(str(settings_path))):
        mcp_config.install_claude_code_hooks()

    assert settings_path.read_text(encoding="utf-8") == original
