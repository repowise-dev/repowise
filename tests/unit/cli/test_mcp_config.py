import json
import re
import sys
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
                            "hooks": [{"type": "command", "command": "repowise augment"}],
                        }
                    ],
                    "PostToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "repowise augment"}],
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
                            "hooks": [{"type": "command", "command": "repowise-augment"}],
                        }
                    ],
                    "PostToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "repowise-augment"}],
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
                            "hooks": [{"type": "command", "command": "repowise augment"}],
                        }
                    ],
                    "PostToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "repowise augment"}],
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


# ---------------------------------------------------------------------------
# Workspace-aware MCP target resolution
#
# Regression coverage for the case where ``repowise init`` is run inside a
# multi-repo workspace. Without workspace-aware resolution, each init
# overwrites ``~/.claude/settings.json`` with the per-repo path, so the
# global MCP server can only see whichever repo was indexed most recently.
# These tests pin the "register the workspace root" behavior in place.
# ---------------------------------------------------------------------------


def _write_workspace_yaml(workspace_root: Path) -> None:
    """Create a minimal valid ``.repowise-workspace.yaml`` at *workspace_root*."""
    (workspace_root / ".repowise-workspace.yaml").write_text(
        "version: 1\nrepos: []\n", encoding="utf-8"
    )


def _registered_repowise_args(settings_path: Path) -> list[str]:
    saved = json.loads(settings_path.read_text(encoding="utf-8"))
    return saved["mcpServers"]["repowise"]["args"]


def test_resolve_mcp_target_returns_repo_path_without_workspace(tmp_path: Path) -> None:
    """Single-repo usage is unchanged: target is the repo path itself."""
    repo = tmp_path / "solo-repo"
    repo.mkdir()
    assert claude_config._resolve_mcp_target(repo) == repo


def test_resolve_mcp_target_returns_workspace_root_when_present(tmp_path: Path) -> None:
    """Inside a workspace, target collapses to the workspace root."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_workspace_yaml(workspace)
    repo = workspace / "child-repo"
    repo.mkdir()

    assert claude_config._resolve_mcp_target(repo) == workspace


def test_resolve_mcp_target_finds_workspace_through_multiple_ancestors(
    tmp_path: Path,
) -> None:
    """``find_workspace_root`` walks up arbitrarily deep, not just one level."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_workspace_yaml(workspace)
    deep = workspace / "a" / "b" / "c" / "repo"
    deep.mkdir(parents=True)

    assert claude_config._resolve_mcp_target(deep) == workspace


def test_register_with_claude_code_uses_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``register_with_claude_code`` against a workspace member targets the root."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_workspace_yaml(workspace)
    repo = workspace / "engine"
    repo.mkdir()

    settings_path = claude_config.register_with_claude_code(repo)
    assert settings_path is not None

    args = _registered_repowise_args(settings_path)
    # ``mcp <path> --transport stdio`` — second element is the target path
    registered_path = Path(args[1])
    assert registered_path == workspace.resolve()


def test_register_with_claude_code_sibling_inits_dont_clobber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug this fix targets: indexing two sibling repos in a workspace
    must converge on a single registration pointing at the workspace root,
    not flip-flop between per-repo paths."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_workspace_yaml(workspace)
    engine = workspace / "engine"
    engine.mkdir()
    ops = workspace / "ops"
    ops.mkdir()

    claude_config.register_with_claude_code(engine)
    first_args = _registered_repowise_args(tmp_path / "home" / ".claude" / "settings.json")

    claude_config.register_with_claude_code(ops)
    second_args = _registered_repowise_args(tmp_path / "home" / ".claude" / "settings.json")

    assert first_args == second_args
    assert Path(first_args[1]) == workspace.resolve()


def test_register_with_claude_code_no_workspace_uses_repo_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a workspace yaml in any ancestor, behavior is unchanged."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    solo = tmp_path / "solo"
    solo.mkdir()

    settings_path = claude_config.register_with_claude_code(solo)
    assert settings_path is not None

    args = _registered_repowise_args(settings_path)
    assert Path(args[1]) == solo.resolve()


def test_register_with_claude_desktop_uses_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``register_with_claude_desktop`` mirrors the workspace-aware behavior."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(sys, "platform", "darwin")

    # Pre-create the Claude Desktop parent dir so the registration proceeds
    desktop_parent = tmp_path / "home" / "Library" / "Application Support" / "Claude"
    desktop_parent.mkdir(parents=True)

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_workspace_yaml(workspace)
    repo = workspace / "engine"
    repo.mkdir()

    config_path = claude_config.register_with_claude_desktop(repo)
    assert config_path is not None

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    args = saved["mcpServers"]["repowise"]["args"]
    assert Path(args[1]) == workspace.resolve()
