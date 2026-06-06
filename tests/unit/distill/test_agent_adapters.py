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
from repowise.cli.agent_adapters.codex import CodexAdapter
from repowise.cli.editor_integrations import claude_config, codex_config
from repowise.cli.editor_integrations.claude_config import (
    claude_code_rewrite_hook_installed,
    install_claude_code_hooks,
    install_claude_code_rewrite_hook,
    migrate_claude_code_hooks,
    uninstall_claude_code_rewrite_hook,
)
from repowise.cli.editor_integrations.codex_config import (
    agents_md_distill_section_installed,
    codex_rewrite_hook_installed,
    codex_supports_rewrite,
    install_agents_md_distill_section,
    install_codex_rewrite_hook,
    remove_agents_md_distill_section,
    uninstall_codex_rewrite_hook,
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
        assert req.shell == "posix"

    def test_valid_powershell_payload(self, adapter) -> None:
        raw = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "PowerShell",
                "tool_input": {"command": "git status"},
                "cwd": "C:\\repo",
            }
        )
        req = adapter.parse_hook_payload(raw)
        assert req is not None
        assert req.command == "git status"
        assert req.shell == "powershell"

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
        assert entries[0]["matcher"] == "Bash|PowerShell"
        hook = entries[0]["hooks"][0]
        assert hook["command"] == "repowise-rewrite"
        assert hook["type"] == "command"
        assert claude_code_rewrite_hook_installed() is True

    def test_idempotent(self, settings_path) -> None:
        install_claude_code_rewrite_hook()
        install_claude_code_rewrite_hook()
        assert len(_pre_hooks(settings_path)) == 1

    def test_install_widens_legacy_bash_matcher(self, settings_path) -> None:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        legacy = {
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": "repowise-rewrite", "timeout": 5}],
        }
        settings_path.write_text(json.dumps({"hooks": {"PreToolUse": [legacy]}}), encoding="utf-8")
        install_claude_code_rewrite_hook()
        entries = _pre_hooks(settings_path)
        assert len(entries) == 1
        assert entries[0]["matcher"] == "Bash|PowerShell"

    def test_install_leaves_user_bash_matcher_alone(self, settings_path) -> None:
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
        assert entries[0]["matcher"] == "Bash"  # user entry untouched
        assert entries[1]["matcher"] == "Bash|PowerShell"

    def test_migrate_widens_legacy_rewrite_matcher(self, settings_path) -> None:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        legacy = {
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": "repowise-rewrite", "timeout": 5}],
        }
        settings_path.write_text(json.dumps({"hooks": {"PreToolUse": [legacy]}}), encoding="utf-8")
        assert migrate_claude_code_hooks() is True
        assert _pre_hooks(settings_path)[0]["matcher"] == "Bash|PowerShell"

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


# ---------------------------------------------------------------------------
# Codex adapter — payload parsing, rendering, posture limits
# ---------------------------------------------------------------------------


@pytest.fixture
def codex_adapter() -> CodexAdapter:
    return CodexAdapter()


class TestCodexParsePayload:
    def test_valid_bash_payload(self, codex_adapter) -> None:
        raw = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "pytest -x"},
                "cwd": "/repo",
            }
        )
        req = codex_adapter.parse_hook_payload(raw)
        assert req is not None
        assert req.command == "pytest -x"
        assert req.cwd == "/repo"
        assert req.shell == "posix"

    @pytest.mark.parametrize(
        "mutation",
        [
            {"hook_event_name": "PostToolUse"},
            {"tool_name": "PowerShell"},  # Codex has no PowerShell tool
            {"tool_name": "apply_patch"},
            {"tool_input": {}},
            {"tool_input": {"command": "   "}},
            {"tool_input": "pytest"},
        ],
    )
    def test_rejects_wrong_shapes(self, codex_adapter, mutation) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest"},
            "cwd": "/repo",
        }
        payload.update(mutation)
        assert codex_adapter.parse_hook_payload(json.dumps(payload)) is None

    @pytest.mark.parametrize("raw", ["", "not json", "[1, 2]", "null"])
    def test_malformed_input_never_raises(self, codex_adapter, raw) -> None:
        assert codex_adapter.parse_hook_payload(raw) is None


class TestCodexRenderResponse:
    def test_shape(self, codex_adapter) -> None:
        result = RewriteResult(
            command="repowise distill --source hook-codex pytest -x",
            permission="allow",
            reason="why",
        )
        rendered = json.loads(codex_adapter.render_response(result))
        assert rendered["hookSpecificOutput"] == {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "why",
            "updatedInput": {"command": "repowise distill --source hook-codex pytest -x"},
        }


class TestRewritePermissions:
    """The capability contract the hook entry point filters on."""

    def test_claude_code_supports_ask_with_mutation(self, adapter) -> None:
        assert adapter.rewrite_permissions == frozenset({"ask", "allow"})

    def test_codex_is_allow_only(self, codex_adapter) -> None:
        assert codex_adapter.rewrite_permissions == frozenset({"allow"})


class TestCodexVersionGate:
    @pytest.mark.parametrize(
        ("version", "expected"),
        [
            ((0, 136), False),
            ((0, 136, 9), False),
            ((0, 137), True),
            ((0, 137, 0), True),
            ((1, 0), True),
        ],
    )
    def test_supports_rewrite_by_version(self, version, expected) -> None:
        assert codex_supports_rewrite(version) is expected

    def test_unknown_version_is_none(self, monkeypatch) -> None:
        monkeypatch.setattr(codex_config, "codex_cli_version", lambda: None)
        assert codex_supports_rewrite() is None


# ---------------------------------------------------------------------------
# ~/.codex/hooks.json install / uninstall
# ---------------------------------------------------------------------------


@pytest.fixture
def codex_hooks_path(tmp_path, monkeypatch):
    path = tmp_path / ".codex" / "hooks.json"
    monkeypatch.setattr(codex_config, "_codex_hooks_path", lambda: path)
    return path


def _codex_pre_hooks(hooks_path) -> list:
    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    return data.get("hooks", {}).get("PreToolUse", [])


class TestCodexHooksInstall:
    def test_fresh_install(self, codex_hooks_path) -> None:
        assert install_codex_rewrite_hook() == codex_hooks_path
        entries = _codex_pre_hooks(codex_hooks_path)
        assert len(entries) == 1
        assert entries[0]["matcher"] == "Bash"
        hook = entries[0]["hooks"][0]
        assert hook["command"] == "repowise-rewrite --agent codex"
        assert codex_rewrite_hook_installed() is True

    def test_idempotent(self, codex_hooks_path) -> None:
        install_codex_rewrite_hook()
        install_codex_rewrite_hook()
        assert len(_codex_pre_hooks(codex_hooks_path)) == 1

    def test_preserves_user_hooks(self, codex_hooks_path) -> None:
        codex_hooks_path.parent.mkdir(parents=True, exist_ok=True)
        user_entry = {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-validator"}]}
        codex_hooks_path.write_text(
            json.dumps({"hooks": {"PreToolUse": [user_entry]}}), encoding="utf-8"
        )
        install_codex_rewrite_hook()
        entries = _codex_pre_hooks(codex_hooks_path)
        assert len(entries) == 2
        assert entries[0]["hooks"][0]["command"] == "my-validator"

    def test_uninstall_removes_and_keeps_user_hooks(self, codex_hooks_path) -> None:
        codex_hooks_path.parent.mkdir(parents=True, exist_ok=True)
        user_entry = {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-validator"}]}
        codex_hooks_path.write_text(
            json.dumps({"hooks": {"PreToolUse": [user_entry]}}), encoding="utf-8"
        )
        install_codex_rewrite_hook()
        assert uninstall_codex_rewrite_hook() is True
        entries = _codex_pre_hooks(codex_hooks_path)
        assert len(entries) == 1
        assert entries[0]["hooks"][0]["command"] == "my-validator"
        assert codex_rewrite_hook_installed() is False

    def test_uninstall_drops_empty_bucket(self, codex_hooks_path) -> None:
        install_codex_rewrite_hook()
        assert uninstall_codex_rewrite_hook() is True
        data = json.loads(codex_hooks_path.read_text(encoding="utf-8"))
        assert "PreToolUse" not in data.get("hooks", {})

    def test_uninstall_when_absent(self, codex_hooks_path) -> None:
        assert uninstall_codex_rewrite_hook() is False


# ---------------------------------------------------------------------------
# AGENTS.md awareness section — install/uninstall must round-trip cleanly
# ---------------------------------------------------------------------------


class TestAgentsMdDistillSection:
    def test_install_creates_file_and_uninstall_deletes_it(self, tmp_path) -> None:
        target = install_agents_md_distill_section(tmp_path)
        assert target == tmp_path / "AGENTS.md"
        content = target.read_text(encoding="utf-8")
        assert "REPOWISE_DISTILL:START" in content
        assert "repowise distill <cmd>" in content
        assert agents_md_distill_section_installed(tmp_path) is True

        assert remove_agents_md_distill_section(tmp_path) is True
        assert not target.exists()  # nothing but our placeholder remained

    def test_round_trips_user_content_byte_for_byte(self, tmp_path) -> None:
        original = "# AGENTS.md\n\nMy own rules.\n\n- never push to main\n"
        (tmp_path / "AGENTS.md").write_text(original, encoding="utf-8", newline="\n")

        install_agents_md_distill_section(tmp_path)
        appended = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert appended.startswith("# AGENTS.md\n\nMy own rules.")
        assert "### Output Distillation" in appended

        assert remove_agents_md_distill_section(tmp_path) is True
        assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == original

    def test_install_is_idempotent(self, tmp_path) -> None:
        install_agents_md_distill_section(tmp_path)
        install_agents_md_distill_section(tmp_path)
        content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert content.count("REPOWISE_DISTILL:START") == 1

    def test_install_refreshes_stale_block_in_place(self, tmp_path) -> None:
        install_agents_md_distill_section(tmp_path)
        target = tmp_path / "AGENTS.md"
        stale = target.read_text(encoding="utf-8").replace("repowise expand", "repowise old-expand")
        target.write_text(stale, encoding="utf-8", newline="\n")

        install_agents_md_distill_section(tmp_path)
        refreshed = target.read_text(encoding="utf-8")
        assert "repowise old-expand" not in refreshed
        assert refreshed.count("REPOWISE_DISTILL:START") == 1

    def test_skips_when_section_already_taught_elsewhere(self, tmp_path) -> None:
        original = "# AGENTS.md\n\n### Output Distillation\n\nAlready covered.\n"
        (tmp_path / "AGENTS.md").write_text(original, encoding="utf-8", newline="\n")
        install_agents_md_distill_section(tmp_path)
        assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == original

    def test_remove_when_absent(self, tmp_path) -> None:
        assert remove_agents_md_distill_section(tmp_path) is False
        assert agents_md_distill_section_installed(tmp_path) is False
