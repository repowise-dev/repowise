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
from repowise.cli.platform.telemetry import emitter, spool
from repowise.cli.platform.telemetry.events import CommandRunEvent


@pytest.fixture(autouse=True)
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point platform.json and the event spool at a temp dir.

    Tests must never touch the real ``~/.repowise``, and a leftover spool file
    would otherwise carry a test's fake events into the next test.
    """
    monkeypatch.setattr(store, "_path", lambda: tmp_path / "platform.json")
    monkeypatch.setattr(spool, "_path", lambda: tmp_path / spool.SPOOL_FILENAME)
    # Clear any inherited telemetry env so each test controls precedence.
    for var in ("DO_NOT_TRACK", "REPOWISE_TELEMETRY_DISABLED", "REPOWISE_TELEMETRY_DEBUG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(emitter, "_flush_thread", None, raising=False)


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
    def test_disabled_queues_nothing(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DO_NOT_TRACK", "1")
        emitter.record(CommandRunEvent(command="health"))
        assert spool.claim() == []

    def test_debug_prints_does_not_queue(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ):
        monkeypatch.setenv("REPOWISE_TELEMETRY_DEBUG", "1")
        emitter.record(CommandRunEvent(command="health"))
        captured = capsys.readouterr()
        assert "would send" in captured.err
        assert spool.claim() == []


class TestSpooledDelivery:
    """Recording queues and exit hands the queue to a detached flusher.

    The command itself must never wait on the network — that is the whole point
    of the spool, and it is what the previous ``atexit`` join gave away.
    """

    def test_record_only_queues(self, monkeypatch: pytest.MonkeyPatch):
        spawned: list = []
        monkeypatch.setattr(emitter, "_spawn_flusher", lambda: spawned.append(True) or True)

        emitter.record(CommandRunEvent(command="health"))

        assert spawned == []  # nothing happens until exit
        assert len(spool.claim()) == 1

    def test_exit_spawns_the_flusher_once_events_are_queued(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        spawned: list = []
        monkeypatch.setattr(emitter, "_spawn_flusher", lambda: spawned.append(True) or True)
        monkeypatch.setattr(emitter, "_under_test", lambda: False)

        emitter._flush()
        assert spawned == []  # empty spool, no process to start

        emitter.record(CommandRunEvent(command="health"))
        emitter._flush()
        assert spawned == [True]

    def test_exit_spawns_nothing_under_pytest(self, monkeypatch: pytest.MonkeyPatch):
        spawned: list = []
        monkeypatch.setattr(emitter, "_spawn_flusher", lambda: spawned.append(True) or True)

        emitter.record(CommandRunEvent(command="health"))
        emitter._flush()

        assert spawned == []

    def test_flusher_posts_and_drains(self, monkeypatch: pytest.MonkeyPatch):
        from repowise.cli.platform import client
        from repowise.cli.platform.telemetry import flusher

        sent: list[dict] = []
        monkeypatch.setattr(
            client.default_client, "post", lambda path, payload, **k: sent.append(payload) or True
        )

        emitter.record(CommandRunEvent(command="health"))
        emitter.record(CommandRunEvent(command="status"))
        assert flusher.deliver() == 2

        assert [e["properties"]["command"] for e in sent] == ["health", "status"]
        # Delivered events are not re-sent by the next flusher.
        assert spool.claim() == []

    def test_disabling_drops_what_is_already_queued(self, monkeypatch: pytest.MonkeyPatch):
        spawned: list = []
        monkeypatch.setattr(emitter, "_spawn_flusher", lambda: spawned.append(True) or True)
        monkeypatch.setattr(emitter, "_under_test", lambda: False)

        emitter.record(CommandRunEvent(command="health"))
        monkeypatch.setenv("DO_NOT_TRACK", "1")
        emitter._flush()

        assert spawned == []
        assert spool.claim() == []

    def test_claim_is_exclusive(self):
        """Two concurrent flushers cannot send the same event twice."""
        emitter.record(CommandRunEvent(command="health"))
        first = spool.claim()
        second = spool.claim()
        assert len(first) == 1
        assert second == []


class TestCommandWrapper:
    def test_status_command_records_one_event(self):
        from repowise.cli.main import cli

        res = CliRunner().invoke(cli, ["telemetry", "status"])
        assert res.exit_code == 0

        queued = spool.claim()
        assert len(queued) == 1
        assert queued[0]["event"] == "command_run"
        assert queued[0]["properties"]["command"] == "telemetry"
        assert queued[0]["properties"]["subcommand"] == "status"
        assert queued[0]["properties"]["status"] == "ok"

    def test_help_records_nothing(self):
        from repowise.cli.main import cli

        res = CliRunner().invoke(cli, ["--help"])
        assert res.exit_code == 0
        assert spool.claim() == []


class TestOutcomeClassification:
    """Ctrl-C / clean-exit must not read as failure (status='error')."""

    def _run(self, monkeypatch: pytest.MonkeyPatch, body) -> list[dict]:
        import click

        from repowise.cli._instrumented_group import InstrumentedGroup

        @click.group(cls=InstrumentedGroup)
        def root() -> None: ...

        @root.command()
        def sub() -> None:
            body()

        CliRunner().invoke(root, ["sub"])
        return spool.claim()

    def test_abort_is_interrupted(self, monkeypatch: pytest.MonkeyPatch):
        import click

        def body() -> None:
            raise click.exceptions.Abort()

        rec = self._run(monkeypatch, body)
        assert rec and rec[0]["properties"]["status"] == "interrupted"

    def test_sigint_exit_code_is_interrupted(self, monkeypatch: pytest.MonkeyPatch):
        def body() -> None:
            raise SystemExit(130)

        rec = self._run(monkeypatch, body)
        assert rec and rec[0]["properties"]["status"] == "interrupted"

    def test_clean_exit_is_ok(self, monkeypatch: pytest.MonkeyPatch):
        def body() -> None:
            raise SystemExit(0)

        rec = self._run(monkeypatch, body)
        assert rec and rec[0]["properties"]["status"] == "ok"

    def test_real_failure_is_error(self, monkeypatch: pytest.MonkeyPatch):
        def body() -> None:
            raise SystemExit(2)

        rec = self._run(monkeypatch, body)
        assert rec and rec[0]["properties"]["status"] == "error"

    def test_usage_error_is_not_error(self, monkeypatch: pytest.MonkeyPatch):
        # A bad/unknown flag is the user mis-invoking the command, not a
        # product failure — it must not inflate the error rate.
        import click

        def body() -> None:
            raise click.NoSuchOption("--bogus")

        rec = self._run(monkeypatch, body)
        assert rec and rec[0]["properties"]["status"] == "usage_error"
        assert rec[0]["properties"]["error_type"] == "NoSuchOption"

    def test_app_guard_stays_error(self, monkeypatch: pytest.MonkeyPatch):
        # A plain ClickException (e.g. "run `repowise init` first") is still a
        # real "could not proceed" outcome and stays in the error bucket.
        import click

        def body() -> None:
            raise click.ClickException("no index found")

        rec = self._run(monkeypatch, body)
        assert rec and rec[0]["properties"]["status"] == "error"

    def test_command_outcome_rides_the_event(self, monkeypatch: pytest.MonkeyPatch):
        from repowise.cli.platform import telemetry

        def body() -> None:
            telemetry.add_command_outcome(file_count_bucket="500-999", docs_mode=False)

        rec = self._run(monkeypatch, body)
        assert rec
        props = rec[0]["properties"]
        assert props["file_count_bucket"] == "500-999"
        assert props["docs_mode"] is False


class TestBucketCount:
    def test_buckets_are_coarse_ranges(self):
        from repowise.cli.platform import telemetry

        assert telemetry.bucket_count(0) == "0"
        assert telemetry.bucket_count(5) == "1-9"
        assert telemetry.bucket_count(742) == "500-999"
        assert telemetry.bucket_count(20000) == "5k+"

    def test_outcome_is_drained_once(self):
        from repowise.cli.platform import telemetry

        telemetry.add_command_outcome(a=1)
        assert telemetry.drain_command_outcome() == {"a": 1}
        # A second drain is empty — the field belongs to one invocation only.
        assert telemetry.drain_command_outcome() == {}
