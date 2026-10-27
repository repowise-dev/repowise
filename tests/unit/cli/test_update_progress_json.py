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

import pytest
from click.testing import CliRunner

from repowise.cli.commands.update_cmd.reporting import JsonProgressEmitter
from repowise.cli.helpers import save_state
from repowise.cli.main import cli
from repowise.core.ingestion.git_indexer import GIT_HISTORY_VERSION, GIT_HISTORY_VERSION_KEY

from ._real_call_guard import guard_real_calls

# An index written by this build: its git rows follow the current rules.
CURRENT_GIT_HISTORY = {GIT_HISTORY_VERSION_KEY: GIT_HISTORY_VERSION}


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
            "outcome": None,
            "pages_generated": 5,
            "cost_usd": 0.42,
            "duration_s": 12.3,
            "degraded": [],
        }

    def test_done_carries_outcome(self, capsys):
        # A supervising agent needs to tell a run that regenerated nothing but
        # deferred to an in-flight update apart from a real regeneration; both
        # otherwise emit ok=True, pages_generated=0.
        JsonProgressEmitter().done(
            ok=True, pages_generated=0, cost_usd=0.0, duration_s=1.0, outcome="deferred"
        )
        (event,) = self._lines(capsys)
        assert event["outcome"] == "deferred"

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

    @pytest.mark.parametrize(
        ("state", "reason"),
        [(None, "no_index"), ('{"total_pages": 3}', "no_sync_base")],
    )
    def test_no_prior_sync_reports_a_reason_code(self, monkeypatch, state, reason):
        from repowise.cli.platform import telemetry

        recorded: dict = {}
        monkeypatch.setattr(telemetry, "add_command_outcome", recorded.update)
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, ".repowise"))
            if state is not None:
                with open(os.path.join(td, ".repowise", "state.json"), "w") as fh:
                    fh.write(state)
            result = _split_output_runner().invoke(cli, ["update", td])

        assert result.exit_code != 0
        assert recorded["failure_reason"] == reason

    def test_up_to_date_emits_start_stage_done(self):
        runner = _split_output_runner()
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, ".repowise"))
            state_path = os.path.join(td, ".repowise", "state.json")
            with open(state_path, "w") as f:
                json.dump({"last_sync_commit": "deadbeef", **CURRENT_GIT_HISTORY}, f)

            result = runner.invoke(cli, ["update", td, "--since", "deadbeef", "--progress", "json"])

        assert result.exit_code == 0
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        events = [json.loads(line) for line in lines]
        assert events[0]["event"] == "start"
        assert events[-1] == {
            "event": "done",
            "ok": True,
            "outcome": "noop",
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


# ---------------------------------------------------------------------------
# run_update call order: pinned before the function is split.
# ---------------------------------------------------------------------------


def _git(repo, *args: str) -> str:
    import subprocess

    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)
    return result.stdout.strip()


def _indexed_repo(tmp_path):
    """A two-file git repo with a real index-only index (no LLM)."""
    import asyncio

    from repowise.core.pipeline.full_index import index_repo_full

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.py").write_text("def alpha():\n    return 1\n")
    (repo / "b.py").write_text("from a import alpha\n\n\ndef beta():\n    return alpha() + 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    asyncio.run(index_repo_full(repo))
    save_state(repo, {"last_sync_commit": _git(repo, "rev-parse", "HEAD"), "docs_enabled": False})
    # A first update writes every stamp a fresh index lacks, so the run under
    # test sees a settled store.
    warm = CliRunner().invoke(cli, ["update", str(repo), "--index-only", "--no-workspace"])
    assert warm.exit_code == 0, warm.output
    _release_lock(repo)
    return repo


def _release_lock(repo) -> None:
    """A content-changing run holds its lock until process exit (atexit), so
    an in-process caller releases it before the next run, as watch does."""
    from repowise.cli.commands.update_cmd.command import clear_update_queued, release_update_lock

    release_update_lock(repo)
    clear_update_queued(repo)


def _spy_calls(monkeypatch) -> list[str]:
    """Wrap the persistence steps in the namespaces run_update reads them from."""
    from repowise.cli.commands.update_cmd import command, persistence

    calls: list[str] = []

    def spy(module, name: str, label: str) -> None:
        real = getattr(module, name)

        def _wrapped(*args, **kwargs):
            calls.append(label)
            return real(*args, **kwargs)

        monkeypatch.setattr(module, name, _wrapped)

    spy(command, "save_state", "save_state")
    spy(persistence, "save_state", "save_state")
    spy(command, "stamp_head_commit", "stamp_head_commit")
    spy(command, "_finish_outcome", "finish_outcome")
    spy(command, "consume_update_pending", "consume_update_pending")
    spy(command, "_record_update_outcome", "record_update_outcome")
    return calls


def _events(result) -> list[dict]:
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


class TestRunUpdateSequence:
    @pytest.fixture(autouse=True)
    def _no_real_calls(self, monkeypatch) -> None:
        # git stays real: the update reads an actual repository.
        guard_real_calls(monkeypatch, allow_git=True)

    def test_no_changed_files_path_order(self, monkeypatch):
        """The --since early return persists state, then stamps, then finishes."""
        calls = _spy_calls(monkeypatch)
        runner = _split_output_runner()
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, ".repowise"))
            with open(os.path.join(td, ".repowise", "state.json"), "w") as f:
                json.dump(
                    {"last_sync_commit": "deadbeef", "docs_mode": "none", **CURRENT_GIT_HISTORY},
                    f,
                )
            result = runner.invoke(
                cli, ["update", td, "--since", "deadbeef", "--progress", "json"]
            )

        assert result.exit_code == 0, result.output
        assert [e["event"] for e in _events(result)] == ["start", "stage", "done"]
        assert _events(result)[1] == {"event": "stage", "name": "detect_changes"}
        assert calls == [
            "save_state",
            "stamp_head_commit",
            "finish_outcome",
            "consume_update_pending",
        ]

    def test_incremental_index_only_update(self, monkeypatch, tmp_path):
        repo = _indexed_repo(tmp_path)
        (repo / "c.py").write_text("def gamma():\n    return 3\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "add c")
        head = _git(repo, "rev-parse", "HEAD")

        calls = _spy_calls(monkeypatch)
        result = _split_output_runner().invoke(
            cli, ["update", str(repo), "--index-only", "--no-workspace", "--progress", "json"]
        )

        assert result.exit_code == 0, result.output
        events = _events(result)
        assert [(e["event"], e.get("name")) for e in events] == [
            ("start", None),
            ("stage", "detect_changes"),
            ("stage", "rebuild_graph"),
            ("stage", "plan_pages"),
            ("stage", "persist"),
            ("done", None),
        ]
        assert events[-1]["ok"] is True
        assert events[-1]["outcome"] == "regenerated"
        # The index-only path stamps the DB head inside its persist step, not
        # through stamp_head_commit.
        assert calls == [
            "save_state",
            "finish_outcome",
            "consume_update_pending",
            "record_update_outcome",
        ]
        state = json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
        assert state["last_sync_commit"] == head

    def test_already_up_to_date_early_return(self, monkeypatch, tmp_path):
        repo = _indexed_repo(tmp_path)
        calls = _spy_calls(monkeypatch)
        result = _split_output_runner().invoke(
            cli, ["update", str(repo), "--index-only", "--no-workspace", "--progress", "json"]
        )

        assert result.exit_code == 0, result.output
        events = _events(result)
        assert [e["event"] for e in events] == ["start", "done"]
        assert events[-1]["outcome"] == "noop"
        assert "Already up to date" in result.stderr
        # No save_state (nothing to backfill) and no outcome record on this path.
        assert calls == ["stamp_head_commit", "finish_outcome", "consume_update_pending"]
