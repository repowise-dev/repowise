"""Doctor and status say when the index no longer describes HEAD (#5232).

Three states used to read exactly like a healthy index: HEAD a few commits
past the last sync, a ``last_sync_commit`` this history does not have, and a
live update that has held ``.update.lock`` for hours.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands import status_cmd
from repowise.cli.commands.doctor_cmd._checks import core
from repowise.cli.commands.doctor_cmd._checks.context import CheckContext
from repowise.core.procutils import process_create_token
from repowise.core.update_lock import UPDATE_LOCK_SUSPECT_AFTER_SECONDS, update_lock_path

NINE_HOURS = 9 * 60 * 60


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with four commits and a ``.repowise/`` directory."""
    _git(tmp_path, "init", "-q")
    for n in range(4):
        (tmp_path / "f.txt").write_text(str(n))
        _git(tmp_path, "add", "f.txt")
        _git(
            tmp_path,
            "-c", "user.name=t", "-c", "user.email=t@example.test", "-c", "commit.gpgsign=false",
            "commit", "-q", "-m", f"c{n}",
        )
    (tmp_path / ".repowise").mkdir()
    return tmp_path


def _hold_lock(repo: Path, held_for: float) -> None:
    """A lock owned by this (live) process, taken *held_for* seconds ago."""
    payload = {
        "pid": os.getpid(),
        "pid_create_token": process_create_token(os.getpid()),
        "target_commit": _git(repo, "rev-parse", "HEAD"),
        "started_at": time.time() - held_for,
    }
    update_lock_path(repo).write_text(json.dumps(payload))


def _rows(repo: Path, last_sync: str) -> dict:
    """The freshness and lock rows, by id, for a state.json naming *last_sync*."""
    state = {"last_sync_commit": last_sync}
    rows = [core._index_freshness_row(repo, state), core._update_lock_row(repo)]
    return {r.id: r for r in rows if r is not None}


def test_index_at_head_is_ok(repo: Path) -> None:
    rows = _rows(repo, _git(repo, "rev-parse", "HEAD"))
    row = rows["index-freshness"]
    assert (row.level, row.ok, row.fix) == ("ok", True, None)
    assert "update-lock" not in rows


def test_index_behind_head_warns_with_update(repo: Path) -> None:
    indexed = _git(repo, "rev-parse", "HEAD~3")
    row = _rows(repo, indexed)["index-freshness"]
    assert (row.level, row.ok, row.fix) == ("warn", True, "repowise update")
    assert indexed[:8] in row.detail
    assert _git(repo, "rev-parse", "HEAD")[:8] in row.detail


def test_sync_commit_missing_from_history_warns_with_since(repo: Path) -> None:
    row = _rows(repo, "12345678")["index-freshness"]
    assert (row.level, row.ok) == ("warn", True)
    assert row.fix == "repowise update --since <ref>"
    assert "12345678 is not in this repository's history" in row.detail


def test_no_row_without_a_commit_to_compare(repo: Path) -> None:
    assert core._index_freshness_row(repo, {}) is None
    assert core._index_freshness_row(repo / ".repowise", {"last_sync_commit": "abc"}) is None


def test_live_lock_past_the_suspect_age_warns_in_update_wording(repo: Path) -> None:
    _hold_lock(repo, NINE_HOURS)
    row = _rows(repo, _git(repo, "rev-parse", "HEAD"))["update-lock"]
    assert (row.level, row.ok) == ("warn", True)
    assert f"An update (pid {os.getpid()}, target " in row.detail
    assert "held the lock for 9.0h" in row.detail
    assert "That is longer than an update normally takes." in row.detail
    assert f"pid {os.getpid()} is stuck and can be stopped" in row.detail


def test_a_young_lock_is_not_reported(repo: Path) -> None:
    _hold_lock(repo, UPDATE_LOCK_SUSPECT_AFTER_SECONDS / 2)
    assert "update-lock" not in _rows(repo, _git(repo, "rev-parse", "HEAD"))


def test_core_checks_place_the_rows_after_state_and_never_fail(repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(core, "_database_rows", lambda _ctx: [])
    monkeypatch.setattr(core, "_runtime_grammar_rows", lambda _p: [])
    (repo / ".repowise" / "state.json").write_text(json.dumps({"last_sync_commit": "12345678"}))
    _hold_lock(repo, NINE_HOURS)
    ctx = CheckContext(repo_path=repo, repowise_dir=repo / ".repowise", quick=True)
    checks = core.core_checks(ctx)
    ids = [c.id for c in checks]
    assert ids[ids.index("state-json") :] == [
        "state-json",
        "index-freshness",
        "store-format",
        "update-lock",
    ]
    new_rows = [c for c in checks if c.id in ("index-freshness", "update-lock")]
    assert [(c.level, c.ok) for c in new_rows] == [("warn", True), ("warn", True)]


def _status(repo: Path, monkeypatch, fmt: str) -> str:
    (repo / ".repowise" / "state.json").write_text(json.dumps({"last_sync_commit": "abc"}))
    monkeypatch.setattr(status_cmd, "db_configured", lambda: False)
    args = [str(repo), "--no-workspace", "--format", fmt]
    result = CliRunner().invoke(status_cmd.status_command, args, terminal_width=400)
    assert result.exit_code == 0, result.output
    return result.output


def test_status_shows_a_stuck_update(repo: Path, monkeypatch) -> None:
    _hold_lock(repo, NINE_HOURS)
    assert "Update lock" in _status(repo, monkeypatch, "table")
    data = json.loads(_status(repo, monkeypatch, "json"))
    assert "held the lock for 9.0h" in data["stuck_update_lock"]


def test_status_says_nothing_of_a_free_lock(repo: Path, monkeypatch) -> None:
    assert "Update lock" not in _status(repo, monkeypatch, "table")
    assert json.loads(_status(repo, monkeypatch, "json"))["stuck_update_lock"] is None
