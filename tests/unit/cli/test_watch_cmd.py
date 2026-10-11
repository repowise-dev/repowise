"""Tests for the ``watch`` command boundary."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from repowise.cli.commands import watch_cmd
from repowise.cli.helpers import CommandTarget
from repowise.cli.main import cli


@pytest.mark.parametrize(
    ("extra_args", "expected_verbose"),
    [([], False), (["--verbose"], True)],
)
def test_watch_configures_logging_before_target_resolution(
    monkeypatch: pytest.MonkeyPatch,
    extra_args: list[str],
    expected_verbose: bool,
) -> None:
    events: list[tuple[str, bool | None]] = []

    def fake_configure_cli_logging(*, verbose: bool = False) -> None:
        events.append(("logging", verbose))

    def fake_resolve_command_target(**_kwargs: object) -> None:
        events.append(("target", None))
        raise click.ClickException("stop after target resolution")

    monkeypatch.setattr(watch_cmd, "configure_cli_logging", fake_configure_cli_logging)
    monkeypatch.setattr(watch_cmd, "resolve_command_target", fake_resolve_command_target)

    result = CliRunner().invoke(cli, ["watch", *extra_args])

    assert result.exit_code == 1
    assert "stop after target resolution" in result.output
    assert events == [("logging", expected_verbose), ("target", None)]


@pytest.mark.parametrize(
    ("extra_args", "expected_verbose"),
    [([], False), (["--verbose"], True)],
)
def test_watch_forwards_logging_mode_to_single_repo_updates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    extra_args: list[str],
    expected_verbose: bool,
) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(watch_cmd, "configure_cli_logging", lambda **_kwargs: None)
    monkeypatch.setattr(
        watch_cmd,
        "resolve_command_target",
        lambda **_kwargs: CommandTarget(mode="single", repo_path=tmp_path),
    )
    monkeypatch.setattr(
        watch_cmd,
        "_watch_single_repo",
        lambda *args: calls.append(args),
    )

    result = CliRunner().invoke(
        cli,
        [
            "watch",
            "--provider",
            "demo-provider",
            "--model",
            "demo-model",
            "--debounce",
            "750",
            *extra_args,
        ],
    )

    assert result.exit_code == 0
    assert calls == [(tmp_path, "demo-provider", "demo-model", 750, expected_verbose, False)]


def test_watch_forwards_index_only(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(watch_cmd, "configure_cli_logging", lambda **_kwargs: None)
    monkeypatch.setattr(
        watch_cmd,
        "resolve_command_target",
        lambda **_kwargs: CommandTarget(mode="single", repo_path=tmp_path),
    )
    monkeypatch.setattr(watch_cmd, "_watch_single_repo", lambda *args: calls.append(args))

    result = CliRunner().invoke(cli, ["watch", "--index-only"])

    assert result.exit_code == 0
    assert calls == [(tmp_path, None, None, 2000, False, True)]


class TestIsWatchablePath:
    """Which filesystem events are worth waking an update for."""

    @pytest.mark.parametrize("path", ["src/app.py", "packages/ui/index.ts", "README.md"])
    def test_source_files_wake_an_update(self, path: str) -> None:
        assert watch_cmd.is_watchable_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            # The traversal blocklist: an `npm install` or a build must not
            # mean thousands of triggers.
            "node_modules/left-pad/index.js",
            "dist/bundle.js",
            ".git/index.lock",
            ".repowise/state.json",
            "uv.lock",
        ],
    )
    def test_ignored_paths(self, path: str) -> None:
        assert watch_cmd.is_watchable_path(path) is False

    @pytest.mark.parametrize(
        "path",
        ["CLAUDE.md", "AGENTS.md", ".mcp.json", ".claude/CLAUDE.md", ".cursor/mcp.json"],
    )
    def test_files_repowise_writes_itself_do_not_retrigger(self, path: str) -> None:
        # Every update re-stamps these. Treating them as user edits would make
        # each update schedule the next one, forever.
        assert watch_cmd.is_watchable_path(path) is False

    def test_windows_separators(self) -> None:
        assert watch_cmd.is_watchable_path(r"src\app.py") is True
        assert watch_cmd.is_watchable_path(r".claude\CLAUDE.md") is False


class TestSingleRepoTrigger:
    """What the debounced trigger actually runs."""

    def _fire(self, monkeypatch: pytest.MonkeyPatch, tmp_path, **watch_kwargs):
        """Start the watcher, touch a file, and return run_update's kwargs."""
        calls: list[dict] = []
        done = threading.Event()

        def fake_run_update(**kwargs):
            calls.append(kwargs)
            done.set()

        monkeypatch.setattr(
            "repowise.cli.commands.update_cmd.command.run_update", fake_run_update
        )
        monkeypatch.setattr(watch_cmd, "ensure_repowise_dir", lambda _p: None)

        started = threading.Event()

        class _Clock:
            """Stands in for the ``time`` module inside watch_cmd only.

            The watcher's idle loop is ``while True: time.sleep(1)``; this lets
            the test break out of it once the update has been observed, instead
            of leaving an Observer thread running for the rest of the session.
            """

            @staticmethod
            def sleep(_seconds: float) -> None:
                started.set()
                time.sleep(0.01)
                if done.is_set():
                    raise KeyboardInterrupt

        monkeypatch.setattr(watch_cmd, "time", _Clock)

        watcher = threading.Thread(
            target=watch_cmd._watch_single_repo,
            args=(tmp_path, None, None, 50, False),
            kwargs=watch_kwargs,
            daemon=True,
        )
        watcher.start()
        assert started.wait(5), "watcher never started"

        (tmp_path / "touched.py").write_text("def x():\n    return 1\n", encoding="utf-8")
        assert done.wait(10), "no update was triggered by the file change"
        watcher.join(timeout=10)
        return calls

    def test_a_file_save_runs_an_update_over_the_working_tree(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        calls = self._fire(monkeypatch, tmp_path)

        assert len(calls) == 1
        # The bug this guards: watch fired an update per save, and the update
        # only ever diffed commit-to-commit, so nothing the watcher saw was
        # ever indexed until the user committed.
        assert calls[0]["include_working_tree"] is True
        assert calls[0]["path"] == str(tmp_path)
        assert calls[0]["index_only"] is False

    def test_index_only_is_passed_through(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        calls = self._fire(monkeypatch, tmp_path, index_only=True)

        assert calls[0]["index_only"] is True


class TestReleaseOwnUpdateLock:
    """``run_update`` only gives the single-flight lock back at process exit.

    Fine for a one-shot CLI run; for a watcher it means the second save finds
    a live lock owned by its own PID and defers, and every save after the
    first is a no-op for the rest of the session.
    """

    def test_a_lock_this_process_owns_is_released(self, tmp_path) -> None:
        from repowise.core.update_lock import try_acquire_update_lock, update_lock_path

        assert try_acquire_update_lock(tmp_path, "abc123") is None
        assert update_lock_path(tmp_path).exists()

        watch_cmd._release_own_update_lock(tmp_path)

        assert not update_lock_path(tmp_path).exists()

    def test_another_process_lock_is_left_alone(self, tmp_path) -> None:
        import json

        from repowise.core.update_lock import update_lock_path

        lock = update_lock_path(tmp_path)
        lock.parent.mkdir(parents=True, exist_ok=True)
        # A live PID that is not ours: PID 1-style payloads are probed for
        # liveness, so use this process's parent-agnostic marker instead —
        # any pid we do not own must survive.
        lock.write_text(
            json.dumps({"pid": os.getpid() + 1, "target_commit": "x", "started_at": time.time()}),
            encoding="utf-8",
        )

        watch_cmd._release_own_update_lock(tmp_path)

        assert lock.exists()

    def test_no_lock_is_not_an_error(self, tmp_path) -> None:
        watch_cmd._release_own_update_lock(tmp_path)


class TestEventPaths:
    """Which paths a filesystem event contributes."""

    class _Event:
        def __init__(self, src_path: str, dest_path: str | None = None) -> None:
            self.src_path = src_path
            self.dest_path = dest_path

    def test_a_plain_write(self, tmp_path) -> None:
        event = self._Event(str(tmp_path / "src" / "app.py"))

        assert watch_cmd._event_paths(event, tmp_path) == {str(Path("src") / "app.py")}

    def test_an_atomic_save_is_seen_through_its_destination(self, tmp_path) -> None:
        # JetBrains IDEs, Vim and many Windows editors save by writing a temp
        # file and renaming it over the target. Reading src_path alone drops
        # the save entirely.
        event = self._Event(
            str(tmp_path / "app.py~RF1a2b.TMP"),
            dest_path=str(tmp_path / "app.py"),
        )

        assert watch_cmd._event_paths(event, tmp_path) == {"app.py"}

    def test_paths_outside_the_root_are_dropped_not_raised(self, tmp_path) -> None:
        event = self._Event(str(tmp_path.parent / "elsewhere" / "app.py"))

        assert watch_cmd._event_paths(event, tmp_path) == set()

    def test_ignored_paths_contribute_nothing(self, tmp_path) -> None:
        event = self._Event(str(tmp_path / ".repowise" / "state.json"))

        assert watch_cmd._event_paths(event, tmp_path) == set()
