"""Tests for the PostToolUse staleness-warning suppression in `repowise augment`.

Covers the noise-reduction behaviors added alongside the docs_enabled work:
1. While `repowise update` holds the update lock, the warning is suppressed.
2. After warning once for a given HEAD, subsequent tool calls don't repeat
   the warning until HEAD moves or the marker is cleared.
3. The warning message reflects whether the repo was indexed with docs.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from repowise.cli.commands.augment_cmd import _handle_bash_post


@pytest.fixture
def repo(tmp_path):
    """A minimal git repo with a `.repowise/state.json`."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--allow-empty", "-m", "init", "-q"],
        cwd=tmp_path,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (tmp_path / ".repowise").mkdir()
    return tmp_path, head


def _state(repo_path: Path, **fields) -> None:
    (repo_path / ".repowise" / "state.json").write_text(
        json.dumps(fields), encoding="utf-8"
    )


def _commit(repo_path: Path) -> str:
    """Make an empty commit and return the new HEAD."""
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--allow-empty", "-m", "next", "-q"],
        cwd=repo_path,
        check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _post(repo_path: Path):
    return _handle_bash_post(
        tool_input={"command": "git commit -m 'x'"},
        tool_output={"exit_code": 0},
        cwd=str(repo_path),
    )


class TestStaleWarning:
    def test_warns_when_state_lags_behind_head(self, repo):
        repo_path, head = repo
        _state(repo_path, last_sync_commit=head, docs_enabled=True)
        new_head = _commit(repo_path)

        msg = _post(repo_path)
        assert msg is not None
        assert "Wiki is stale" in msg
        assert head[:8] in msg
        assert new_head[:8] in msg

    def test_no_warning_when_in_sync(self, repo):
        repo_path, head = repo
        _state(repo_path, last_sync_commit=head, docs_enabled=True)

        assert _post(repo_path) is None

    def test_no_warning_when_no_state_file(self, repo):
        repo_path, _ = repo
        # state.json never written
        assert _post(repo_path) is None


class TestUpdateLockSuppression:
    def test_lock_emits_in_flight_notice_not_stale_warning(self, repo):
        # When an update is running, the agent should hear "updating in
        # background" instead of "wiki is stale". The original behavior was
        # to suppress entirely (return None); the explicit positive notice
        # is better UX because the agent can plan around it.
        repo_path, head = repo
        _state(repo_path, last_sync_commit=head, docs_enabled=True)
        new_head = _commit(repo_path)

        import os
        import time

        # A live PID: read_update_lock now probes liveness, so a fabricated
        # dead PID would (correctly) be treated as a crashed update.
        (repo_path / ".repowise" / ".update.lock").write_text(
            json.dumps({
                "pid": os.getpid(),
                "target_commit": new_head,
                "started_at": time.time(),
            }),
            encoding="utf-8",
        )

        msg = _post(repo_path)
        assert msg is not None
        assert "update in background" in msg
        assert "stale" not in msg.lower()

    def test_lock_from_dead_pid_does_not_suppress(self, repo):
        # A crashed update's leftover lock must NOT suppress the stale
        # warning for the rest of the 30-min window — the dead owner makes
        # it stale immediately.
        import subprocess
        import sys
        import time

        repo_path, head = repo
        _state(repo_path, last_sync_commit=head, docs_enabled=True)
        _commit(repo_path)

        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait(timeout=30)
        (repo_path / ".repowise" / ".update.lock").write_text(
            json.dumps({
                "pid": proc.pid,
                "target_commit": "x",
                "started_at": time.time(),
            }),
            encoding="utf-8",
        )

        msg = _post(repo_path)
        assert msg is not None
        assert "stale" in msg.lower()

    def test_stale_lock_does_not_suppress(self, repo):
        repo_path, head = repo
        _state(repo_path, last_sync_commit=head, docs_enabled=True)
        _commit(repo_path)

        # Lock from 2 hours ago — beyond the 30-min stale window
        (repo_path / ".repowise" / ".update.lock").write_text(
            json.dumps({"pid": 1, "target_commit": "x", "started_at": 0}),
            encoding="utf-8",
        )

        msg = _post(repo_path)
        assert msg is not None
        assert "stale" in msg.lower()  # Falls through to the real stale warning

    def test_lock_for_different_commit_still_signals_in_flight(self, repo):
        # An update is running but its target predates HEAD. We still emit
        # the in-flight notice (not the stale warning): the running update
        # will narrow the gap, and the next tool call will re-evaluate.
        repo_path, head = repo
        _state(repo_path, last_sync_commit=head, docs_enabled=True)
        _commit(repo_path)

        import os
        import time

        (repo_path / ".repowise" / ".update.lock").write_text(
            json.dumps({
                "pid": os.getpid(),  # live owner — see liveness probe note above
                "target_commit": head,  # old HEAD, not the current one
                "started_at": time.time(),
            }),
            encoding="utf-8",
        )

        msg = _post(repo_path)
        assert msg is not None
        assert "update in background" in msg


class TestQueuedMarkerSuppression:
    """The post-commit hook drops ``.update.queued`` before backgrounding the
    update. The augment hook must treat that marker the same as a held lock,
    or every commit in a rapid burst will get a noisy stale warning during
    the start-up window before the real lock file lands on disk."""

    def test_queued_marker_emits_in_flight_notice(self, repo):
        repo_path, head = repo
        _state(repo_path, last_sync_commit=head, docs_enabled=True)
        new_head = _commit(repo_path)

        import time

        (repo_path / ".repowise" / ".update.queued").write_text(
            json.dumps({"target_commit": new_head, "queued_at": time.time()}),
            encoding="utf-8",
        )

        msg = _post(repo_path)
        assert msg is not None
        assert "update in background" in msg

    def test_stale_queued_marker_falls_through_to_stale_warning(self, repo):
        # The queued marker has a much shorter staleness window (5 min) than
        # the real lock (30 min) — a queued marker that age means the hook
        # spawned an update but it never reached the lock-acquire step.
        repo_path, head = repo
        _state(repo_path, last_sync_commit=head, docs_enabled=True)
        _commit(repo_path)

        (repo_path / ".repowise" / ".update.queued").write_text(
            json.dumps({"target_commit": "x", "queued_at": 0}),
            encoding="utf-8",
        )

        msg = _post(repo_path)
        assert msg is not None
        assert "stale" in msg.lower()


class TestPerHeadDedupe:
    def test_warns_once_per_head(self, repo):
        repo_path, head = repo
        _state(repo_path, last_sync_commit=head, docs_enabled=True)
        _commit(repo_path)

        first = _post(repo_path)
        second = _post(repo_path)

        assert first is not None
        assert second is None  # Suppressed by per-HEAD marker

    def test_warning_re_fires_on_new_commit(self, repo):
        repo_path, head = repo
        _state(repo_path, last_sync_commit=head, docs_enabled=True)
        _commit(repo_path)
        _post(repo_path)  # First warning, marker written

        _commit(repo_path)  # New HEAD invalidates marker
        msg = _post(repo_path)
        assert msg is not None


class TestDocsEnabledWording:
    def test_says_index_when_docs_disabled(self, repo):
        repo_path, head = repo
        _state(repo_path, last_sync_commit=head, docs_enabled=False)
        _commit(repo_path)

        msg = _post(repo_path)
        assert msg is not None
        assert "Index is stale" in msg
        assert "Wiki is stale" not in msg

    def test_says_wiki_when_docs_enabled(self, repo):
        repo_path, head = repo
        _state(repo_path, last_sync_commit=head, docs_enabled=True)
        _commit(repo_path)

        msg = _post(repo_path)
        assert "Wiki is stale" in msg

    def test_defaults_to_wiki_when_field_missing(self, repo):
        # Backward compat: pre-existing state.json files without
        # docs_enabled should keep the old wording.
        repo_path, head = repo
        _state(repo_path, last_sync_commit=head)
        _commit(repo_path)

        msg = _post(repo_path)
        assert "Wiki is stale" in msg
