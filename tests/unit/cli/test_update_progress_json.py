"""Tests for `repowise update --progress json`.

Covers the ``JsonProgressEmitter`` in isolation (each event serializes to one
valid JSON line) and a ``CliRunner`` smoke test of the wiring: stdout carries
only the event stream, informational Rich output moves to stderr, and the
stream is well-formed on both the "nothing to do" and "no prior sync" paths
(the only ones cheaply reachable without a real provider/LLM call).
"""

from __future__ import annotations

import json
import os
import tempfile

from click.testing import CliRunner

from repowise.cli.commands.update_cmd.reporting import JsonProgressEmitter
from repowise.cli.main import cli


class TestJsonProgressEmitter:
    def _lines(self, capsys) -> list[dict]:
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line.strip()]
        return [json.loads(line) for line in lines]

    def test_start(self, capsys):
        JsonProgressEmitter().start(repo="myrepo", since="abc123")
        (event,) = self._lines(capsys)
        assert event == {"event": "start", "repo": "myrepo", "since": "abc123"}

    def test_stage(self, capsys):
        JsonProgressEmitter().stage("rebuild_graph")
        (event,) = self._lines(capsys)
        assert event == {"event": "stage", "name": "rebuild_graph"}

    def test_total_known(self, capsys):
        JsonProgressEmitter().total_known(42)
        (event,) = self._lines(capsys)
        assert event == {"event": "total_known", "total": 42}

    def test_page_done(self, capsys):
        JsonProgressEmitter().page_done(completed=3, total=42, cost_usd=0.015)
        (event,) = self._lines(capsys)
        assert event == {"event": "page_done", "completed": 3, "total": 42, "cost_usd": 0.015}

    def test_page_done_total_unknown(self, capsys):
        JsonProgressEmitter().page_done(completed=1, total=None, cost_usd=0.0)
        (event,) = self._lines(capsys)
        assert event["total"] is None

    def test_done(self, capsys):
        JsonProgressEmitter().done(ok=True, pages_generated=5, cost_usd=0.42, duration_s=12.3)
        (event,) = self._lines(capsys)
        assert event == {
            "event": "done",
            "ok": True,
            "pages_generated": 5,
            "cost_usd": 0.42,
            "duration_s": 12.3,
            "degraded": [],
        }

    def test_done_reports_degraded_steps(self, capsys):
        JsonProgressEmitter().done(
            ok=True,
            pages_generated=5,
            cost_usd=0.42,
            duration_s=12.3,
            degraded=["Git persist: disk I/O error"],
        )
        (event,) = self._lines(capsys)
        assert event["degraded"] == ["Git persist: disk I/O error"]

    def test_error(self, capsys):
        JsonProgressEmitter().error("boom")
        (event,) = self._lines(capsys)
        assert event == {"event": "error", "message": "boom"}

    def test_multiple_events_are_one_json_object_per_line(self, capsys):
        emitter = JsonProgressEmitter()
        emitter.start(repo="r", since=None)
        emitter.stage("generate")
        emitter.total_known(2)
        emitter.page_done(completed=1, total=2, cost_usd=0.01)
        emitter.page_done(completed=2, total=2, cost_usd=0.02)
        emitter.done(ok=True, pages_generated=2, cost_usd=0.02, duration_s=1.0)

        events = self._lines(capsys)
        assert [e["event"] for e in events] == [
            "start",
            "stage",
            "total_known",
            "page_done",
            "page_done",
            "done",
        ]


def _split_output_runner() -> CliRunner:
    """CliRunner with stdout and stderr separated, across click versions.

    click < 8.2 mixes the streams unless ``mix_stderr=False``; click 8.2
    removed the parameter and separates them by default.
    """
    try:
        return CliRunner(mix_stderr=False)  # type: ignore[call-arg]
    except TypeError:
        return CliRunner()


class TestUpdateProgressJsonCli:
    """CliRunner smoke tests against the cheap no-provider/no-LLM early-exit paths."""

    def test_no_prior_sync_emits_start_then_error(self):
        runner = _split_output_runner()
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, ".repowise"))
            result = runner.invoke(cli, ["update", td, "--progress", "json"])

        assert result.exit_code != 0
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        events = [json.loads(line) for line in lines]
        assert [e["event"] for e in events] == ["start", "error"]
        assert "No previous sync found" in events[1]["message"]
        # stdout carries nothing but the two JSON lines.
        for line in lines:
            json.loads(line)  # would raise if any non-JSON line leaked through

    def test_up_to_date_emits_start_stage_done(self):
        runner = _split_output_runner()
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, ".repowise"))
            state_path = os.path.join(td, ".repowise", "state.json")
            with open(state_path, "w") as f:
                json.dump({"last_sync_commit": "deadbeef"}, f)

            result = runner.invoke(cli, ["update", td, "--since", "deadbeef", "--progress", "json"])

        assert result.exit_code == 0
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        events = [json.loads(line) for line in lines]
        assert events[0]["event"] == "start"
        assert events[-1] == {
            "event": "done",
            "ok": True,
            "pages_generated": 0,
            "cost_usd": 0.0,
            "duration_s": events[-1]["duration_s"],
            "degraded": [],
        }
        # Informational Rich output (repo header, "No changed files detected.")
        # went to stderr, not stdout.
        assert "No changed files detected" not in result.stdout
        assert "No changed files detected" in result.stderr

    def test_rich_mode_is_unaffected_default(self):
        """Default --progress rich must not emit any JSON lines."""
        runner = _split_output_runner()
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, ".repowise"))
            result = runner.invoke(cli, ["update", td])

        assert result.exit_code != 0
        for line in result.stdout.splitlines():
            if line.strip():
                assert not line.strip().startswith("{")
