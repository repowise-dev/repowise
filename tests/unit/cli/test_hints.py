"""The repowise.dev hints: when they show, what they say, where they link.

What protects the user: at most one line per run on stderr, each hint at most
once a week, never for agents, daemons, CI, JSON output or signed-in users,
and every way to turn them off works.
"""

from __future__ import annotations

import io
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

import click
import pytest
from click.testing import CliRunner
from rich.console import Console

from repowise.cli import hints
from repowise.cli.platform import credentials, store
from repowise.cli.platform.telemetry import environment


@pytest.fixture
def stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """A terminal-like stderr outside CI, signed out, with a fresh store."""
    monkeypatch.setattr(credentials, "_path", lambda: tmp_path / "credentials.json")
    monkeypatch.setattr(store, "_path", lambda: tmp_path / "platform.json")
    store.update(anon_id="abc123def456")
    for var in ("REPOWISE_NO_HINTS", "DO_NOT_TRACK", "REPOWISE_TELEMETRY_DISABLED"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(environment, "is_ci", lambda: False)
    monkeypatch.setattr("sys.stderr.isatty", lambda: True, raising=False)
    buf = io.StringIO()
    import repowise.cli.helpers as helpers

    monkeypatch.setattr(helpers, "err_console", Console(file=buf, width=300, highlight=False))
    monkeypatch.setattr(hints, "_shown_this_run", False)
    return buf


def _url(text: str) -> str:
    return next(w for w in text.split() if w.startswith("https://repowise.dev/hosted"))


class TestShowing:
    def test_shows_the_line_its_action_and_its_link(self, stderr, monkeypatch):
        from repowise.cli.platform import telemetry

        recorded: dict = {}
        monkeypatch.setattr(telemetry, "add_command_outcome", lambda **f: recorded.update(f))

        assert hints.maybe_hint("init_success") is True

        out = stderr.getvalue()
        assert "Use this repo from Claude.ai or ChatGPT, or share it with your team." in out
        assert "Publish it free: repowise publish" in out
        url = urlparse(_url(out))
        assert url.path == "/hosted" and url.fragment == "mcp"
        assert parse_qs(url.query) == {"src": ["cli_init_success"], "aid": ["abc123def456"]}
        assert recorded == {"hint_shown": "init_success"}

    def test_link_has_no_install_id_with_telemetry_off(self, stderr, monkeypatch):
        monkeypatch.setenv("REPOWISE_TELEMETRY_DISABLED", "1")
        hints.maybe_hint("slow_update")
        assert parse_qs(urlparse(_url(stderr.getvalue())).query) == {"src": ["cli_slow_update"]}

    def test_one_hint_per_run(self, stderr):
        assert hints.maybe_hint("init_success")
        assert not hints.maybe_hint("slow_update")
        assert "Skip manual updates" not in stderr.getvalue()

    def test_once_a_week_per_hint(self, stderr, monkeypatch):
        assert hints.maybe_hint("slow_update")
        monkeypatch.setattr(hints, "_shown_this_run", False)
        assert not hints.maybe_hint("slow_update")

        week_ago = (datetime.now(UTC) - timedelta(days=7, minutes=1)).isoformat()
        store.update(hints_shown={"slow_update": week_ago})
        assert hints.maybe_hint("slow_update")

    def test_another_hint_is_not_blocked_by_the_week(self, stderr, monkeypatch):
        assert hints.maybe_hint("slow_update")
        monkeypatch.setattr(hints, "_shown_this_run", False)
        assert hints.maybe_hint("init_success")


class TestNeverShown:
    @pytest.mark.parametrize("var", ["REPOWISE_NO_HINTS", "DO_NOT_TRACK"])
    def test_env_switches(self, stderr, monkeypatch, var):
        monkeypatch.setenv(var, "1")
        assert not hints.maybe_hint("init_success")
        assert stderr.getvalue() == ""

    def test_config_switch(self, stderr):
        result = CliRunner().invoke(_cli(), ["config", "hints", "off"])
        assert result.exit_code == 0, result.output
        assert not hints.maybe_hint("init_success")
        CliRunner().invoke(_cli(), ["config", "hints", "on"])
        assert hints.maybe_hint("init_success")

    def test_ci(self, stderr, monkeypatch):
        monkeypatch.setattr(environment, "is_ci", lambda: True)
        assert not hints.maybe_hint("init_success")

    def test_not_a_terminal(self, stderr, monkeypatch):
        monkeypatch.setattr("sys.stderr.isatty", lambda: False, raising=False)
        assert not hints.maybe_hint("init_success")

    def test_json_output(self, stderr):
        assert not hints.maybe_hint("ask_nokey", fmt="json")

    def test_signed_in(self, stderr):
        credentials.save(
            {"token_kind": "api_key", "access_token": "rw_live_x", "expires": time.time()}
        )
        assert not hints.maybe_hint("init_success")

    @pytest.mark.parametrize("command", ["mcp", "serve", "watch", "hook", "augment"])
    def test_agent_daemon_and_hook_commands(self, stderr, command):
        @click.group()
        def root() -> None:
            pass

        shown: list[bool] = []

        @root.command(name=command)
        def sub() -> None:
            # CliRunner swaps stderr for a pipe; make it a terminal again so
            # only the command name can stop the hint.
            with mock.patch("sys.stderr.isatty", return_value=True):
                shown.append(hints.maybe_hint("slow_update"))

        @root.command(name="update")
        def other() -> None:
            with mock.patch("sys.stderr.isatty", return_value=True):
                shown.append(hints.maybe_hint("slow_update"))

        CliRunner().invoke(root, [command])
        assert shown == [False]
        CliRunner().invoke(root, ["update"])
        assert shown == [False, True]

    def test_unknown_hint(self, stderr):
        assert not hints.maybe_hint("nope")


class TestWording:
    def test_every_hint_ends_with_a_free_publish_step(self):
        for hint in hints.HINTS.values():
            assert hint.action.endswith("repowise publish")
            assert hint.action.split(":")[0] in {"Publish it free", "Start free"}

    def test_trial_claims_name_the_card(self):
        for hint in hints.HINTS.values():
            text = f"{hint.text} {hint.action}".lower()
            if "trial" in text or "10 days" in text or "private" in text:
                assert "free for 10 days, card required" in text

    def test_moments_exist_on_the_story_page(self):
        moments = {"sync", "mcp", "bot", "security", "link", "team", "keys"}
        assert {h.moment for h in hints.HINTS.values()} <= moments
        assert all(h.src.startswith("cli_") for h in hints.HINTS.values())


class TestPlacement:
    def test_init_picks_the_large_repo_hint_for_big_or_fast_runs(self):
        from repowise.cli.commands.init_cmd.reporting import completion_hint

        assert completion_hint("standard", 999) == "init_success"
        assert completion_hint("standard", 1000) == "large_repo"
        assert completion_hint("fast", 10) == "large_repo"

    @pytest.mark.parametrize(("elapsed", "expected"), [(59.0, []), (61.0, ["slow_update"])])
    def test_update_hints_only_when_slow(self, monkeypatch, elapsed, expected):
        from repowise.cli.commands.update_cmd import reporting

        asked: list[str] = []
        monkeypatch.setattr(hints, "maybe_hint", lambda hint_id, **kw: asked.append(hint_id))
        reporting._slow_update_hint(elapsed)
        assert asked == expected

    def test_ask_without_a_key_hints(self, monkeypatch, tmp_path):
        from repowise.cli.commands import ask_cmd

        asked: list[tuple[str, str]] = []
        monkeypatch.setattr(
            hints, "maybe_hint", lambda hint_id, fmt="text": asked.append((hint_id, fmt))
        )
        payload = {"answer": "", "degraded": "no-llm-provider"}
        monkeypatch.setattr(ask_cmd._ta, "run", lambda *a, **k: payload)
        monkeypatch.setattr(ask_cmd._ta, "resolve_indexed_repo", lambda **k: tmp_path)
        monkeypatch.setattr(ask_cmd._ta, "print_index_note", lambda *a, **k: None)
        monkeypatch.setattr(ask_cmd._ta, "emit_error", lambda *a, **k: None)
        result = CliRunner().invoke(_cli(), ["ask", "how?", "--path", str(tmp_path)])
        assert asked == [("ask_nokey", "table")], result.output


def _cli():
    from repowise.cli.main import cli

    return cli
