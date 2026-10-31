"""``repowise init`` opt-in flow for the distill command-rewrite hook."""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest
import yaml

from repowise.cli.agent_adapters.claude_code import ClaudeCodeAdapter
from repowise.cli.commands.init_cmd._interactive import offer_distill_rewrite_hook
from repowise.cli.editor_integrations import claude_config
from repowise.cli.helpers import HOOK_REPLACEMENT_SURFACES


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


@pytest.fixture
def workspace_repos(tmp_path):
    """Three sibling repos as the workspace flow would have prepared them."""
    repos = []
    for name in ("alpha", "beta", "gamma"):
        (tmp_path / "ws" / name / ".repowise").mkdir(parents=True)
        repos.append(tmp_path / "ws" / name)
    return repos


def _distill_config(repo) -> dict:
    cfg = yaml.safe_load((repo / ".repowise" / "config.yaml").read_text(encoding="utf-8"))
    return cfg.get("distill", {})


def _hooks_config(repo) -> dict:
    cfg = yaml.safe_load((repo / ".repowise" / "config.yaml").read_text(encoding="utf-8"))
    return cfg.get("hooks", {})


def _installed_commands(settings_path) -> list[str]:
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    return [h["command"] for e in data["hooks"]["PreToolUse"] for h in e["hooks"]]


class TestOfferDistillRewriteHook:
    def test_explicit_optin_installs_and_enables(self, settings_path, repo) -> None:
        offer_distill_rewrite_hook(MagicMock(), [repo], flag=True)
        assert settings_path.exists()
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        commands = [h["command"] for e in data["hooks"]["PreToolUse"] for h in e["hooks"]]
        assert commands == ["repowise-rewrite"]
        assert _distill_config(repo)["commands"]["enabled"] is True

    def test_explicit_optout_gates_repo_off(self, settings_path, repo) -> None:
        offer_distill_rewrite_hook(MagicMock(), [repo], flag=False)
        assert not settings_path.exists()
        assert _distill_config(repo)["commands"]["enabled"] is False

    @pytest.mark.parametrize(("tty", "yes"), [(False, False), (True, True)])
    def test_no_flag_without_a_prompt_turns_every_surface_on(
        self, settings_path, repo, monkeypatch, tty, yes
    ) -> None:
        monkeypatch.setattr(sys.stdin, "isatty", lambda: tty)
        monkeypatch.setattr(ClaudeCodeAdapter, "detect", lambda self: True)
        console = MagicMock()
        offer_distill_rewrite_hook(console, [repo], flag=None, yes=yes)
        assert _installed_commands(settings_path) == ["repowise-rewrite"]
        assert _distill_config(repo)["commands"]["enabled"] is True
        assert _hooks_config(repo) == dict.fromkeys(HOOK_REPLACEMENT_SURFACES, True)
        printed = " ".join(str(c.args[0]) for c in console.print.call_args_list if c.args)
        assert "--no-distill-hook" in printed

    def test_no_flag_without_claude_code_does_nothing(self, settings_path, repo, monkeypatch) -> None:
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(ClaudeCodeAdapter, "detect", lambda self: False)
        offer_distill_rewrite_hook(MagicMock(), [repo], flag=None)
        assert not settings_path.exists()
        assert not (repo / ".repowise" / "config.yaml").exists()

    @pytest.mark.parametrize("tty", [False, True])
    def test_no_flag_keeps_a_recorded_optout(self, settings_path, repo, monkeypatch, tty) -> None:
        monkeypatch.setattr(sys.stdin, "isatty", lambda: tty)
        monkeypatch.setattr(ClaudeCodeAdapter, "detect", lambda self: True)
        monkeypatch.setattr("click.confirm", lambda *a, **k: pytest.fail("asked again"))
        offer_distill_rewrite_hook(MagicMock(), [repo], flag=False)
        before = (repo / ".repowise" / "config.yaml").read_text(encoding="utf-8")
        offer_distill_rewrite_hook(MagicMock(), [repo], flag=None)
        assert not settings_path.exists()
        assert (repo / ".repowise" / "config.yaml").read_text(encoding="utf-8") == before

    def test_no_flag_keeps_a_surface_switched_off_by_hand(
        self, settings_path, repo, monkeypatch
    ) -> None:
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(ClaudeCodeAdapter, "detect", lambda self: True)
        (repo / ".repowise" / "config.yaml").write_text(
            "hooks:\n  read_reread: false\n", encoding="utf-8"
        )
        offer_distill_rewrite_hook(MagicMock(), [repo], flag=None)
        hooks = _hooks_config(repo)
        assert hooks.pop("read_reread") is False
        assert set(hooks.values()) == {True}

    def test_no_flag_with_editor_setup_off_writes_nothing(
        self, settings_path, repo, monkeypatch
    ) -> None:
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(ClaudeCodeAdapter, "detect", lambda self: True)
        offer_distill_rewrite_hook(MagicMock(), [repo], flag=None, yes=True, no_editor_setup=True)
        assert not settings_path.exists()
        assert not (repo / ".repowise" / "config.yaml").exists()

    def test_skip_editor_setup_env_blocks_install(self, settings_path, repo, monkeypatch) -> None:
        monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
        offer_distill_rewrite_hook(MagicMock(), [repo], flag=True)
        assert not settings_path.exists()
        assert not (repo / ".repowise" / "config.yaml").exists()

    def test_optout_preserves_existing_distill_config(self, settings_path, repo) -> None:
        (repo / ".repowise" / "config.yaml").write_text(
            yaml.dump({"distill": {"commands": {"disabled_filters": ["git_diff"]}}}),
            encoding="utf-8",
        )
        offer_distill_rewrite_hook(MagicMock(), [repo], flag=False)
        distill = _distill_config(repo)
        assert distill["commands"]["enabled"] is False
        assert distill["commands"]["disabled_filters"] == ["git_diff"]

    def test_empty_repo_list_is_a_noop(self, settings_path) -> None:
        offer_distill_rewrite_hook(MagicMock(), [], flag=True)
        assert not settings_path.exists()


class TestWorkspaceOptIn:
    """The workspace flow records one verdict across every selected repo."""

    def test_optin_installs_once_and_enables_all_repos(
        self, settings_path, workspace_repos
    ) -> None:
        offer_distill_rewrite_hook(MagicMock(), workspace_repos, flag=True)
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        commands = [h["command"] for e in data["hooks"]["PreToolUse"] for h in e["hooks"]]
        # User-level hook installed exactly once, not per repo.
        assert commands == ["repowise-rewrite"]
        for rp in workspace_repos:
            assert _distill_config(rp)["commands"]["enabled"] is True

    def test_optout_gates_every_repo_off(self, settings_path, workspace_repos) -> None:
        offer_distill_rewrite_hook(MagicMock(), workspace_repos, flag=False)
        assert not settings_path.exists()
        for rp in workspace_repos:
            assert _distill_config(rp)["commands"]["enabled"] is False

    def test_interactive_decline_gates_every_repo_off(
        self, settings_path, workspace_repos, monkeypatch
    ) -> None:
        import sys

        import click

        from repowise.cli.agent_adapters.claude_code import ClaudeCodeAdapter

        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(ClaudeCodeAdapter, "detect", lambda self: True)
        monkeypatch.setattr(click, "confirm", lambda *a, **k: False)
        offer_distill_rewrite_hook(MagicMock(), workspace_repos, flag=None)
        assert not settings_path.exists()
        for rp in workspace_repos:
            assert _distill_config(rp)["commands"]["enabled"] is False

    def test_interactive_accept_enables_every_repo(
        self, settings_path, workspace_repos, monkeypatch
    ) -> None:
        import sys

        import click

        from repowise.cli.agent_adapters.claude_code import ClaudeCodeAdapter

        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(ClaudeCodeAdapter, "detect", lambda self: True)
        monkeypatch.setattr(click, "confirm", lambda *a, **k: True)
        offer_distill_rewrite_hook(MagicMock(), workspace_repos, flag=None)
        assert settings_path.exists()
        for rp in workspace_repos:
            assert _distill_config(rp)["commands"]["enabled"] is True

    def test_one_bad_repo_does_not_abort_the_rest(
        self, settings_path, workspace_repos, monkeypatch
    ) -> None:
        from repowise.cli import helpers

        original = helpers.save_distill_commands_enabled
        bad = workspace_repos[0]

        def flaky(repo_path, *, enabled):
            if repo_path == bad:
                raise OSError("disk full")
            original(repo_path, enabled=enabled)

        monkeypatch.setattr(helpers, "save_distill_commands_enabled", flaky)
        offer_distill_rewrite_hook(MagicMock(), workspace_repos, flag=False)
        for rp in workspace_repos[1:]:
            assert _distill_config(rp)["commands"]["enabled"] is False


def _enable_plugin(settings_path, hooks_json: str) -> None:
    """An enabled repowise plugin carrying *hooks_json*, laid out as Claude Code keeps it."""
    install = settings_path.parent / "plugins" / "cache" / "repowise" / "9.9.9"
    (install / "hooks").mkdir(parents=True)
    (install / "hooks" / "hooks.json").write_text(hooks_json, encoding="utf-8")
    (settings_path.parent / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"plugins": {"repowise@repowise": [{"installPath": str(install)}]}}),
        encoding="utf-8",
    )
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["enabledPlugins"] = {"repowise@repowise": True}
    settings_path.write_text(json.dumps(settings), encoding="utf-8")


def test_a_plugin_that_ships_the_rewrite_hook_is_not_doubled(settings_path, repo) -> None:
    """Claude Code runs a plugin hook and a settings hook separately."""
    from pathlib import Path

    shipped = Path(__file__).parents[3] / "plugins" / "claude-code" / "hooks" / "hooks.json"
    claude_config.install_claude_code_rewrite_hook()
    assert claude_config._settings_rewrite_matcher() is not None
    _enable_plugin(settings_path, shipped.read_text(encoding="utf-8"))

    offer_distill_rewrite_hook(MagicMock(), [repo], flag=True)

    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert not data.get("hooks", {}).get("PreToolUse")
    assert claude_config.claude_code_rewrite_hook_matcher() == claude_config._REWRITE_MATCHER
    assert _distill_config(repo)["commands"]["enabled"] is True
