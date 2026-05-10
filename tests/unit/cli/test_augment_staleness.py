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
    def test_lock_suppresses_warning(self, repo):
        repo_path, head = repo
        _state(repo_path, last_sync_commit=head, docs_enabled=True)
        new_head = _commit(repo_path)

        # Simulate `repowise update` running with target_commit=new_head
        import time

        (repo_path / ".repowise" / ".update.lock").write_text(
            json.dumps({
                "pid": 999999,
                "target_commit": new_head,
                "started_at": time.time(),
            }),
            encoding="utf-8",
        )

        assert _post(repo_path) is None

    def test_stale_lock_does_not_suppress(self, repo):
        repo_path, head = repo
        _state(repo_path, last_sync_commit=head, docs_enabled=True)
        _commit(repo_path)

        # Lock from 2 hours ago — beyond the 30-min stale window
        (repo_path / ".repowise" / ".update.lock").write_text(
            json.dumps({"pid": 1, "target_commit": "x", "started_at": 0}),
            encoding="utf-8",
        )

        assert _post(repo_path) is not None

    def test_lock_for_different_commit_still_suppresses(self, repo):
        # An update is running but its target predates HEAD. We still
        # suppress: the in-flight run will at least narrow the gap, and
        # the next tool call will re-evaluate.
        repo_path, head = repo
        _state(repo_path, last_sync_commit=head, docs_enabled=True)
        _commit(repo_path)

        import time

        (repo_path / ".repowise" / ".update.lock").write_text(
            json.dumps({
                "pid": 1,
                "target_commit": head,  # old HEAD, not the current one
                "started_at": time.time(),
            }),
            encoding="utf-8",
        )

        assert _post(repo_path) is None


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
