"""Agent-adapter seam — Claude Code payload parsing, rendering, hook install.

The adapter owns everything Claude-Code-specific; these tests pin the
protocol shapes and prove the settings.json install/uninstall is idempotent,
migration-safe, and preserves user hooks and the augment PostToolUse entry.
"""

from __future__ import annotations

import json

import pytest

from repowise.cli.agent_adapters.base import RewriteResult
from repowise.cli.agent_adapters.claude_code import ClaudeCodeAdapter
from repowise.cli.editor_integrations import claude_config
from repowise.cli.editor_integrations.claude_config import (
    claude_code_rewrite_hook_installed,
    install_claude_code_hooks,
    install_claude_code_rewrite_hook,
    migrate_claude_code_hooks,
    uninstall_claude_code_rewrite_hook,
)


@pytest.fixture
def adapter() -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter()


class TestParsePayload:
    def test_valid_bash_payload(self, adapter) -> None:
        raw = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "pytest -x"},
                "cwd": "/repo",
            }
        )
        req = adapter.parse_hook_payload(raw)
        assert req is not None
        assert req.command == "pytest -x"
        assert req.cwd == "/repo"

    @pytest.mark.parametrize(
        "mutation",
        [
            {"hook_event_name": "PostToolUse"},
            {"tool_name": "Grep"},
            {"tool_input": {}},
            {"tool_input": {"command": "   "}},
            {"tool_input": "pytest"},
        ],
    )
    def test_rejects_wrong_shapes(self, adapter, mutation) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest"},
            "cwd": "/repo",
        }
        payload.update(mutation)
        assert adapter.parse_hook_payload(json.dumps(payload)) is None

    @pytest.mark.parametrize("raw", ["", "not json", "[1, 2]", "null"])
    def test_malformed_input_never_raises(self, adapter, raw) -> None:
        assert adapter.parse_hook_payload(raw) is None

    def test_missing_cwd_defaults_empty(self, adapter) -> None:
        raw = json.dumps(
            {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}
        )
        req = adapter.parse_hook_payload(raw)
        assert req is not None and req.cwd == ""


class TestRenderResponse:
    def test_shape(self, adapter) -> None:
        result = RewriteResult(command="repowise distill pytest -x", permission="ask", reason="why")
        rendered = json.loads(adapter.render_response(result))
        hso = rendered["hookSpecificOutput"]
        assert hso == {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": "why",
            "updatedInput": {"command": "repowise distill pytest -x"},
        }


# ---------------------------------------------------------------------------
# settings.json install / uninstall
# ---------------------------------------------------------------------------


@pytest.fixture
def settings_path(tmp_path, monkeypatch):
    path = tmp_path / ".claude" / "settings.json"
    monkeypatch.setattr(claude_config, "_claude_code_settings_path", lambda: path)
    return path


def _read(settings_path) -> dict:
    return json.loads(settings_path.read_text(encoding="utf-8"))


def _pre_hooks(settings_path) -> list:
    return _read(settings_path).get("hooks", {}).get("PreToolUse", [])


class TestRewriteHookInstall:
    def test_fresh_install(self, settings_path) -> None:
        assert install_claude_code_rewrite_hook() == settings_path
        entries = _pre_hooks(settings_path)
        assert len(entries) == 1
        assert entries[0]["matcher"] == "Bash"
        hook = entries[0]["hooks"][0]
        assert hook["command"] == "repowise-rewrite"
        assert hook["type"] == "command"
        assert claude_code_rewrite_hook_installed() is True

    def test_idempotent(self, settings_path) -> None:
        install_claude_code_rewrite_hook()
        install_claude_code_rewrite_hook()
        assert len(_pre_hooks(settings_path)) == 1

    def test_preserves_user_pretool_hooks(self, settings_path) -> None:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        user_entry = {
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": "my-validator"}],
        }
        settings_path.write_text(
            json.dumps({"hooks": {"PreToolUse": [user_entry]}}), encoding="utf-8"
        )
        install_claude_code_rewrite_hook()
        entries = _pre_hooks(settings_path)
        assert len(entries) == 2
        assert entries[0]["hooks"][0]["command"] == "my-validator"

    def test_uninstall_removes_and_drops_empty_bucket(self, settings_path) -> None:
        install_claude_code_rewrite_hook()
        assert uninstall_claude_code_rewrite_hook() is True
        assert "PreToolUse" not in _read(settings_path).get("hooks", {})
        assert claude_code_rewrite_hook_installed() is False

    def test_uninstall_keeps_user_hooks(self, settings_path) -> None:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        user_entry = {
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": "my-validator"}],
        }
        settings_path.write_text(
            json.dumps({"hooks": {"PreToolUse": [user_entry]}}), encoding="utf-8"
        )
        install_claude_code_rewrite_hook()
        assert uninstall_claude_code_rewrite_hook() is True
        entries = _pre_hooks(settings_path)
        assert len(entries) == 1
        assert entries[0]["hooks"][0]["command"] == "my-validator"

    def test_uninstall_when_absent(self, settings_path) -> None:
        assert uninstall_claude_code_rewrite_hook() is False


class TestCoexistenceWithAugmentHooks:
    """The PostToolUse installer and the legacy migration historically strip
    repowise PreToolUse entries — the rewrite hook must survive both."""

    def test_post_install_preserves_rewrite_hook(self, settings_path) -> None:
        install_claude_code_rewrite_hook()
        install_claude_code_hooks()
        assert claude_code_rewrite_hook_installed() is True
        post = _read(settings_path)["hooks"]["PostToolUse"]
        assert any("repowise-augment" in h["command"] for e in post for h in e["hooks"])

    def test_migration_preserves_rewrite_hook(self, settings_path) -> None:
        install_claude_code_rewrite_hook()
        # Seed a legacy augment PreToolUse entry that migration must strip.
        existing = _read(settings_path)
        existing["hooks"]["PreToolUse"].append(
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "repowise augment"}]}
        )
        settings_path.write_text(json.dumps(existing), encoding="utf-8")

        assert migrate_claude_code_hooks() is True
        entries = _pre_hooks(settings_path)
        commands = [h["command"] for e in entries for h in e["hooks"]]
        assert commands == ["repowise-rewrite"]

    def test_rewrite_install_preserves_augment_post_hook(self, settings_path) -> None:
        install_claude_code_hooks()
        install_claude_code_rewrite_hook()
        data = _read(settings_path)
        post = data["hooks"]["PostToolUse"]
        assert any("repowise-augment" in h["command"] for e in post for h in e["hooks"])
        assert claude_code_rewrite_hook_installed() is True


class TestAdapterDelegation:
    def test_install_uninstall_via_adapter(self, settings_path, adapter) -> None:
        assert adapter.install_rewrite_hook() == settings_path
        assert adapter.rewrite_hook_installed() is True
        assert adapter.uninstall_rewrite_hook() is True
        assert adapter.rewrite_hook_installed() is False
