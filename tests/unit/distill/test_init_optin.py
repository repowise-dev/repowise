"""``repowise init`` opt-in flow for the distill command-rewrite hook."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import yaml

from repowise.cli.commands.init_cmd._interactive import offer_distill_rewrite_hook
from repowise.cli.editor_integrations import claude_config


@pytest.fixture
def settings_path(tmp_path, monkeypatch):
    path = tmp_path / "home" / ".claude" / "settings.json"
    monkeypatch.setattr(claude_config, "_claude_code_settings_path", lambda: path)
    monkeypatch.delenv("REPOWISE_SKIP_EDITOR_SETUP", raising=False)
    return path


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "repo" / ".repowise").mkdir(parents=True)
    return tmp_path / "repo"


def _distill_config(repo) -> dict:
    cfg = yaml.safe_load((repo / ".repowise" / "config.yaml").read_text(encoding="utf-8"))
    return cfg.get("distill", {})


class TestOfferDistillRewriteHook:
    def test_explicit_optin_installs_and_enables(self, settings_path, repo) -> None:
        offer_distill_rewrite_hook(MagicMock(), repo, flag=True)
        assert settings_path.exists()
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        commands = [h["command"] for e in data["hooks"]["PreToolUse"] for h in e["hooks"]]
        assert commands == ["repowise-rewrite"]
        assert _distill_config(repo)["commands"]["enabled"] is True

    def test_explicit_optout_gates_repo_off(self, settings_path, repo) -> None:
        offer_distill_rewrite_hook(MagicMock(), repo, flag=False)
        assert not settings_path.exists()
        assert _distill_config(repo)["commands"]["enabled"] is False

    def test_no_flag_noninteractive_does_nothing(self, settings_path, repo, monkeypatch) -> None:
        import sys

        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        offer_distill_rewrite_hook(MagicMock(), repo, flag=None)
        assert not settings_path.exists()
        assert not (repo / ".repowise" / "config.yaml").exists()

    def test_skip_editor_setup_env_blocks_install(self, settings_path, repo, monkeypatch) -> None:
        monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
        offer_distill_rewrite_hook(MagicMock(), repo, flag=True)
        assert not settings_path.exists()

    def test_optout_preserves_existing_distill_config(self, settings_path, repo) -> None:
        (repo / ".repowise" / "config.yaml").write_text(
            yaml.dump({"distill": {"commands": {"disabled_filters": ["git_diff"]}}}),
            encoding="utf-8",
        )
        offer_distill_rewrite_hook(MagicMock(), repo, flag=False)
        distill = _distill_config(repo)
        assert distill["commands"]["enabled"] is False
        assert distill["commands"]["disabled_filters"] == ["git_diff"]
