"""Permission allow-rule seeding for distilled commands.

A rewrite changes the command string, so the user's existing Claude Code
allowlist entries (``Bash(git diff:*)``) stop matching the rewritten
``repowise distill git diff …``. ``repowise hook rewrite install`` offers
to seed one allow rule per shell tool covering the distill prefix; these
tests pin the settings.json mechanics — additive, idempotent, never
touching user rules — and the CLI flag plumbing.
"""

from __future__ import annotations

import json

import pytest

from repowise.cli.editor_integrations import claude_config
from repowise.cli.editor_integrations.claude_config import (
    DISTILL_ALLOW_RULES,
    add_claude_code_distill_allow_rules,
)


@pytest.fixture
def settings_path(tmp_path, monkeypatch):
    path = tmp_path / ".claude" / "settings.json"
    monkeypatch.setattr(claude_config, "_claude_code_settings_path", lambda: path)
    return path


def _allow(settings_path) -> list:
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    return data.get("permissions", {}).get("allow", [])


class TestAddDistillAllowRules:
    def test_creates_missing_file(self, settings_path) -> None:
        assert add_claude_code_distill_allow_rules() == settings_path
        assert _allow(settings_path) == list(DISTILL_ALLOW_RULES)

    def test_rules_cover_both_shell_tools(self) -> None:
        assert "Bash(repowise distill:*)" in DISTILL_ALLOW_RULES
        assert "PowerShell(repowise distill:*)" in DISTILL_ALLOW_RULES

    def test_idempotent(self, settings_path) -> None:
        add_claude_code_distill_allow_rules()
        before = settings_path.read_text(encoding="utf-8")
        add_claude_code_distill_allow_rules()
        assert settings_path.read_text(encoding="utf-8") == before

    def test_preserves_existing_user_rules(self, settings_path) -> None:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(
                {
                    "permissions": {"allow": ["Bash(git status:*)"], "deny": ["WebFetch"]},
                    "hooks": {},
                }
            ),
            encoding="utf-8",
        )
        add_claude_code_distill_allow_rules()
        allow = _allow(settings_path)
        assert allow[0] == "Bash(git status:*)"
        for rule in DISTILL_ALLOW_RULES:
            assert rule in allow
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert data["permissions"]["deny"] == ["WebFetch"]

    def test_partial_presence_adds_only_missing(self, settings_path) -> None:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps({"permissions": {"allow": ["Bash(repowise distill:*)"]}}),
            encoding="utf-8",
        )
        add_claude_code_distill_allow_rules()
        allow = _allow(settings_path)
        assert allow.count("Bash(repowise distill:*)") == 1
        assert "PowerShell(repowise distill:*)" in allow

    def test_malformed_settings_returns_none(self, settings_path) -> None:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text("{not json", encoding="utf-8")
        assert add_claude_code_distill_allow_rules() is None


class TestRewriteInstallFlag:
    """--allow-rule / --no-allow-rule plumbing on `hook rewrite install`."""

    @pytest.fixture
    def repo(self, tmp_path, monkeypatch):
        (tmp_path / ".repowise").mkdir()
        monkeypatch.chdir(tmp_path)
        return tmp_path

    def _invoke(self, args):
        from click.testing import CliRunner

        from repowise.cli.commands.hook_cmd import hook_group

        return CliRunner().invoke(hook_group, args)

    def test_allow_rule_flag_seeds_rules(self, repo, settings_path) -> None:
        result = self._invoke(["rewrite", "install", "--allow-rule", str(repo)])
        assert result.exit_code == 0, result.output
        assert "Allow rule added" in result.output
        for rule in DISTILL_ALLOW_RULES:
            assert rule in _allow(settings_path)

    def test_no_allow_rule_flag_skips(self, repo, settings_path) -> None:
        result = self._invoke(["rewrite", "install", "--no-allow-rule", str(repo)])
        assert result.exit_code == 0, result.output
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert "permissions" not in data

    def test_default_adds_no_rule(self, repo, settings_path) -> None:
        # The default `allow` posture rewrites without a prompt, so install
        # seeds no allowlist entry unless --allow-rule is passed explicitly.
        result = self._invoke(["rewrite", "install", str(repo)])
        assert result.exit_code == 0, result.output
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert "permissions" not in data
