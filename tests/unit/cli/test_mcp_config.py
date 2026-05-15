import json
import re
from pathlib import Path

import click
import pytest

from repowise.cli import mcp_config
from repowise.cli.editor_integrations import claude_config


def _repowise_entry(repo_path: Path) -> dict:
    return mcp_config.generate_mcp_config(repo_path)["mcpServers"]


# ---------------------------------------------------------------------------
# Root .mcp.json
# ---------------------------------------------------------------------------


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

    assert mcp_config.merge_mcp_entry(config_path, _repowise_entry(tmp_path))

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

    assert mcp_config.merge_mcp_entry(config_path, _repowise_entry(tmp_path))

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["mcpServers"]["existing"] == {"command": "existing"}
    assert "repowise" in saved["mcpServers"]
    assert saved["permissions"] == {"allow": ["Bash(git status:*)"]}


def test_merge_mcp_entry_rejects_invalid_existing_file(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.json"
    original = '{\n  "permissions": {},\n}\n'
    config_path.write_text(original, encoding="utf-8")

    with pytest.raises(click.ClickException, match=re.escape(str(config_path))):
        mcp_config.merge_mcp_entry(config_path, _repowise_entry(tmp_path))

    assert config_path.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# install_claude_code_hooks — fresh installs
# ---------------------------------------------------------------------------


def _post_repowise_entries(saved: dict) -> list:
    return [
        (entry.get("matcher"), h["command"])
        for entry in saved["hooks"].get("PostToolUse", [])
        for h in entry["hooks"]
        if "repowise" in h.get("command", "")
    ]


def test_install_claude_code_hooks_creates_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh install: only a PostToolUse entry is added. The new design
    routes Bash + Grep + Glob through a single matcher; PreToolUse is
    intentionally absent because it can't see actual result counts."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    settings_path = claude_config.install_claude_code_hooks()

    assert settings_path == tmp_path / ".claude" / "settings.json"
    saved = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "PreToolUse" not in saved["hooks"]
    assert _post_repowise_entries(saved) == [("Bash|Grep|Glob", "repowise-augment")]


def test_install_claude_code_hooks_preserves_user_pretool_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User-defined PreToolUse hooks (non-repowise) are never touched."""
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

    assert claude_config.install_claude_code_hooks() == settings_path

    saved = json.loads(settings_path.read_text(encoding="utf-8"))
    assert saved["permissions"] == {"allow": ["Bash(git status:*)"]}
    # User's PreToolUse Read hook stays intact.
    assert saved["hooks"]["PreToolUse"][0]["matcher"] == "Read"
    assert saved["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "echo read"
    # PostToolUse now has the repowise hook.
    assert _post_repowise_entries(saved) == [("Bash|Grep|Glob", "repowise-augment")]


# ---------------------------------------------------------------------------
# install_claude_code_hooks — migrating existing repowise installs
# ---------------------------------------------------------------------------


def test_install_claude_code_hooks_migrates_pre_0_6_1_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-0.6.1 entries used the legacy ``repowise augment`` Click command.
    Installer should drop the PreToolUse repowise entry entirely (the new
    design moves enrichment to PostToolUse) and rewrite + widen the
    PostToolUse entry to ``Bash|Grep|Glob`` with ``repowise-augment``."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Grep|Glob",
                            "hooks": [
                                {"type": "command", "command": "repowise augment"}
                            ],
                        }
                    ],
                    "PostToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {"type": "command", "command": "repowise augment"}
                            ],
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    assert claude_config.install_claude_code_hooks() == settings_path

    saved = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "PreToolUse" not in saved["hooks"]
    assert _post_repowise_entries(saved) == [("Bash|Grep|Glob", "repowise-augment")]


def test_install_claude_code_hooks_migrates_pre_0_6_2_matcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.6.1 entries had ``repowise-augment`` already but kept matcher=Bash.
    The matcher should widen to ``Bash|Grep|Glob`` so the same hook covers
    Grep/Glob enrichment too."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Grep|Glob",
                            "hooks": [
                                {"type": "command", "command": "repowise-augment"}
                            ],
                        }
                    ],
                    "PostToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {"type": "command", "command": "repowise-augment"}
                            ],
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    assert claude_config.install_claude_code_hooks() == settings_path

    saved = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "PreToolUse" not in saved["hooks"]
    assert _post_repowise_entries(saved) == [("Bash|Grep|Glob", "repowise-augment")]


def test_install_claude_code_hooks_idempotent_on_current_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running install twice should leave settings.json in the same shape."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    claude_config.install_claude_code_hooks()
    first = (tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8")
    claude_config.install_claude_code_hooks()
    second = (tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8")
    assert first == second


# ---------------------------------------------------------------------------
# migrate_claude_code_hooks — self-heal path
# ---------------------------------------------------------------------------


def test_migrate_claude_code_hooks_handles_full_legacy_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Self-heal on a pre-0.6.1 settings.json: drop PreToolUse repowise
    entry, migrate command + matcher on PostToolUse, write back."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Grep|Glob",
                            "hooks": [
                                {"type": "command", "command": "repowise augment"}
                            ],
                        }
                    ],
                    "PostToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {"type": "command", "command": "repowise augment"}
                            ],
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    assert claude_config.migrate_claude_code_hooks() is True

    saved = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "PreToolUse" not in saved["hooks"]
    assert _post_repowise_entries(saved) == [("Bash|Grep|Glob", "repowise-augment")]

    # Idempotent: a second run finds nothing to do.
    assert claude_config.migrate_claude_code_hooks() is False


def test_migrate_claude_code_hooks_preserves_user_sibling_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PreToolUse entry with a user-defined sibling hook in the same
    matcher block should keep the sibling and only drop the repowise hook."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Grep|Glob",
                            "hooks": [
                                {"type": "command", "command": "repowise-augment"},
                                {"type": "command", "command": "echo hi"},
                            ],
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    assert claude_config.migrate_claude_code_hooks() is True
    saved = json.loads(settings_path.read_text(encoding="utf-8"))
    pre = saved["hooks"]["PreToolUse"]
    assert len(pre) == 1
    assert [h["command"] for h in pre[0]["hooks"]] == ["echo hi"]


def test_migrate_claude_code_hooks_noop_when_already_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No write when settings.json is already in the current shape."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    payload = {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Bash|Grep|Glob",
                    "hooks": [{"type": "command", "command": "repowise-augment"}],
                }
            ]
        }
    }
    original = json.dumps(payload, indent=2) + "\n"
    settings_path.write_text(original, encoding="utf-8")
    mtime_before = settings_path.stat().st_mtime_ns

    assert claude_config.migrate_claude_code_hooks() is False
    assert settings_path.read_text(encoding="utf-8") == original
    assert settings_path.stat().st_mtime_ns == mtime_before


def test_migrate_claude_code_hooks_silent_when_settings_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert claude_config.migrate_claude_code_hooks() is False


def test_migrate_claude_code_hooks_silent_on_malformed_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{ not json", encoding="utf-8")

    assert claude_config.migrate_claude_code_hooks() is False


def test_install_claude_code_hooks_rejects_invalid_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    original = '{\n  "permissions": {},\n}\n'
    settings_path.write_text(original, encoding="utf-8")

    with pytest.raises(click.ClickException, match=re.escape(str(settings_path))):
        claude_config.install_claude_code_hooks()

    assert settings_path.read_text(encoding="utf-8") == original
