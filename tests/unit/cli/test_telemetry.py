"""Tests for anonymous, opt-out CLI telemetry (the ``platform`` layer).

Covers the three things that protect user trust: consent precedence (env vars
beat stored state, opt-out default), the privacy shape of the wire envelope
(only anonymous fields, flag names not values, no patch-version leak), and the
central command wrapper recording exactly one event without breaking commands.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.platform import identity, settings, store
from repowise.cli.platform.telemetry import emitter
from repowise.cli.platform.telemetry.events import CommandRunEvent


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point platform.json at a temp dir so tests never touch real ~/.repowise."""
    monkeypatch.setattr(store, "_path", lambda: tmp_path / "platform.json")
    # Clear any inherited telemetry env so each test controls precedence.
    for var in ("DO_NOT_TRACK", "REPOWISE_TELEMETRY_DISABLED", "REPOWISE_TELEMETRY_DEBUG"):
        monkeypatch.delenv(var, raising=False)
    # Isolate the module-global pending-send list between tests.
    emitter._pending.clear()
    yield
    emitter._pending.clear()


class TestConsentPrecedence:
    def test_opt_out_default_enabled(self):
        assert settings.is_enabled() is True
        assert settings.disabled_reason() is None

    def test_do_not_track_wins(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DO_NOT_TRACK", "1")
        assert settings.is_enabled() is False
        assert settings.disabled_reason() == "DO_NOT_TRACK is set"

    def test_tool_specific_env_disables(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("REPOWISE_TELEMETRY_DISABLED", "true")
        assert settings.is_enabled() is False

    def test_stored_disable_then_enable(self):
        settings.set_enabled(False)
        assert settings.is_enabled() is False
        assert "disable" in (settings.disabled_reason() or "")
        settings.set_enabled(True)
        assert settings.is_enabled() is True

    def test_env_beats_stored_enable(self, monkeypatch: pytest.MonkeyPatch):
        settings.set_enabled(True)
        monkeypatch.setenv("DO_NOT_TRACK", "1")
        assert settings.is_enabled() is False


class TestIdentity:
    def test_anon_id_stable_and_opaque(self):
        first = identity.get_anonymous_id()
        assert isinstance(first, str) and len(first) >= 16
        assert identity.get_anonymous_id() == first  # persisted, stable

    def test_anon_id_not_derived_from_machine(self):
        # Two fresh stores must yield different ids — proof it is random, not a
        # hash of stable machine identifiers.
        store.update(anon_id="")  # reset
        a = identity.get_anonymous_id()
        store.save({})  # wipe
        b = identity.get_anonymous_id()
        assert a != b


class TestEnvelopePrivacy:
    def test_envelope_shape(self):
        ev = CommandRunEvent(
            command="init",
            subcommand=None,
            flags=["--resume", "--provider"],
            status="ok",
            duration_ms=1234,
        )
        env = emitter.build_envelope(ev)
        assert env["event"] == "command_run"
        assert {
            "anon_id",
            "session_id",
            "cli_version",
            "os",
            "arch",
            "python_version",
            "is_ci",
            "properties",
        } <= set(env)

    def test_no_patch_version_leak(self):
        env = emitter.build_envelope(CommandRunEvent(command="status"))
        assert env["python_version"].count(".") == 1  # major.minor only

    def test_flags_carry_no_values(self):
        env = emitter.build_envelope(
            CommandRunEvent(command="update", flags=["--provider", "--exclude"])
        )
        for flag in env["properties"]["flags"]:
            assert flag.startswith("--")
            assert "=" not in flag


class TestFlagNormalization:
    """`_option_name` must never let an option value reach an event."""

    def test_long_value_stripped(self):
        from repowise.cli._instrumented_group import _option_name

        assert _option_name("--provider=openai") == "--provider"
        assert _option_name("--no-cost-tracking") == "--no-cost-tracking"

    def test_attached_short_value_stripped(self):
        from repowise.cli._instrumented_group import _option_name

        # The PII leak the review caught: attached short-option values.
        assert _option_name("-p/home/me/secret-project") == "-p"
        assert _option_name("-x*internal_codename*") == "-x"
        assert _option_name("-o/abs/output/path") == "-o"

    def test_plain_short_and_combined(self):
        from repowise.cli._instrumented_group import _option_name

        assert _option_name("-v") == "-v"
        assert _option_name("-vv") == "-v"

    def test_extra_extension_point(self):
        env = emitter.build_envelope(
            CommandRunEvent(command="init", extra={"file_count_bucket": "500-1k"})
        )
        assert env["properties"]["file_count_bucket"] == "500-1k"


class TestEmitterRespectsConsent:
    def test_disabled_sends_nothing(self, monkeypatch: pytest.MonkeyPatch):
        sent: list = []
        monkeypatch.setattr(emitter.default_client, "post", lambda *a, **k: sent.append(a) or True)
        monkeypatch.setenv("DO_NOT_TRACK", "1")
        emitter.record(CommandRunEvent(command="health"))
        emitter._flush()
        assert sent == []

    def test_debug_prints_does_not_send(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ):
        sent: list = []
        monkeypatch.setattr(emitter.default_client, "post", lambda *a, **k: sent.append(a) or True)
        monkeypatch.setenv("REPOWISE_TELEMETRY_DEBUG", "1")
        emitter.record(CommandRunEvent(command="health"))
        captured = capsys.readouterr()
        assert "would send" in captured.err
        assert sent == []


class TestCommandWrapper:
    def test_status_command_records_one_event(self, monkeypatch: pytest.MonkeyPatch):
        from repowise.cli.main import cli

        recorded: list[dict] = []

        def fake_post(path, payload, **kwargs):
            recorded.append(payload)
            return True

        monkeypatch.setattr(emitter.default_client, "post", fake_post)
        # Force synchronous delivery so we can assert without races.
        monkeypatch.setattr(
            emitter.threading,
            "Thread",
            lambda target, args, daemon: type(
                "T", (), {"start": lambda s: target(*args), "join": lambda s, timeout=None: None}
            )(),
        )

        res = CliRunner().invoke(cli, ["telemetry", "status"])
        assert res.exit_code == 0
        assert len(recorded) == 1
        assert recorded[0]["event"] == "command_run"
        assert recorded[0]["properties"]["command"] == "telemetry"
        assert recorded[0]["properties"]["subcommand"] == "status"
        assert recorded[0]["properties"]["status"] == "ok"

    def test_help_records_nothing(self, monkeypatch: pytest.MonkeyPatch):
        from repowise.cli.main import cli

        recorded: list = []
        monkeypatch.setattr(
            emitter.default_client, "post", lambda *a, **k: recorded.append(a) or True
        )
        res = CliRunner().invoke(cli, ["--help"])
        assert res.exit_code == 0
        assert recorded == []
