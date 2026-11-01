"""Installed hooks that never run say so: the doctor row and the SessionStart sentence."""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from repowise.cli import hook_ledger
from repowise.cli.commands.augment_cmd import session_start
from repowise.cli.commands.doctor_cmd._checks import integrations
from repowise.core.sessions import transcript_dir_for

_DAY = 24 * 3600
_NOW = time.time()


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def repo(tmp_path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".repowise").mkdir(parents=True)
    return repo


def _install_hooks(home: Path, *, at: float, rewrite: bool = False) -> None:
    settings = home / ".claude" / "settings.json"
    entry = {"matcher": "startup|resume|clear", "hooks": [
        {"type": "command", "command": "repowise-augment", "timeout": 10}]}
    hooks: dict = {"SessionStart": [entry]}
    if rewrite:
        hooks["PreToolUse"] = [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "repowise-rewrite"}]}]
    settings.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")
    os.utime(settings, (at, at))


def _session(repo: Path, session_id: str, started: float) -> None:
    """A Claude Code transcript for *repo*, started and last written at *started*."""
    directory = transcript_dir_for(repo.resolve())
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.fromtimestamp(started, datetime.UTC).isoformat().replace("+00:00", "Z")
    path = directory / f"{session_id}.jsonl"
    path.write_text(json.dumps({"type": "user", "timestamp": stamp}) + "\n", encoding="utf-8")
    os.utime(path, (started + 60, started + 60))


def _fired(repo: Path, *session_ids: str) -> None:
    db_dir = repo / ".repowise" / "sessions"
    db_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_dir / "sessions.db")
    conn.execute(hook_ledger._HOOK_RUNS_TABLE_SQL)
    conn.executemany(
        "INSERT INTO hook_runs (session_id, event, tool, calls) VALUES (?, 'SessionStart', '', 1)",
        [(sid,) for sid in session_ids],
    )
    conn.commit()
    conn.close()


def test_no_row_when_no_hook_is_installed(home, repo) -> None:
    _session(repo, "s1", _NOW - _DAY)
    assert integrations._hooks_firing_row(repo) is None


def test_a_fresh_install_does_not_warn(home, repo) -> None:
    # Months of sessions before the install, none since: nothing to judge yet.
    for i in range(5):
        _session(repo, f"old{i}", _NOW - (30 + i) * _DAY)
    _install_hooks(home, at=_NOW - _DAY)
    row = integrations._hooks_firing_row(repo)
    assert row is not None
    assert row.level == "ok"
    assert "no Claude Code session here has run them yet" in row.detail


def test_a_machine_without_claude_code_sessions_does_not_warn(home, repo) -> None:
    _install_hooks(home, at=_NOW - 10 * _DAY)
    row = integrations._hooks_firing_row(repo)
    assert row is not None and row.level == "ok"


def test_hooks_that_fired_recently_do_not_warn(home, repo) -> None:
    _install_hooks(home, at=_NOW - 10 * _DAY)
    for i in range(4):
        _session(repo, f"s{i}", _NOW - (5 - i) * _DAY)
    _fired(repo, "s0", "s1", "s2", "s3")
    row = integrations._hooks_firing_row(repo)
    assert row is not None
    assert row.level == "ok"
    assert row.detail.startswith("last ran ")


def test_sessions_after_the_last_firing_warn_with_its_date(home, repo) -> None:
    _install_hooks(home, at=_NOW - 20 * _DAY)
    _session(repo, "ran", _NOW - 16 * _DAY)
    _fired(repo, "ran")
    for i in range(3):
        _session(repo, f"silent{i}", _NOW - (10 - i) * _DAY)
    row = integrations._hooks_firing_row(repo)
    assert row is not None
    assert row.level == "warn"
    last = datetime.datetime.fromtimestamp(_NOW - 16 * _DAY + 60).strftime("%Y-%m-%d")
    assert f"have not run since {last}" in row.detail
    assert "3 Claude Code sessions" in row.detail
    assert row.fix == "repowise agents refresh"


def test_hooks_that_never_ran_warn(home, repo) -> None:
    _install_hooks(home, at=_NOW - 20 * _DAY)
    for i in range(3):
        _session(repo, f"silent{i}", _NOW - (10 - i) * _DAY)
    row = integrations._hooks_firing_row(repo)
    assert row is not None
    assert row.level == "warn"
    assert "have not run in any session" in row.detail


def test_two_silent_sessions_are_not_enough_to_warn(home, repo) -> None:
    _install_hooks(home, at=_NOW - 20 * _DAY)
    _session(repo, "ran", _NOW - 16 * _DAY)
    _fired(repo, "ran")
    _session(repo, "silent0", _NOW - 3 * _DAY)
    _session(repo, "silent1", _NOW - 2 * _DAY)
    assert integrations._hooks_firing_row(repo).level == "ok"


def test_the_ledger_read_never_creates_the_database(repo) -> None:
    assert hook_ledger.ledger_session_ids(repo) == set()
    assert not (repo / ".repowise" / "sessions" / "sessions.db").exists()


def test_the_doctor_row_is_wired_into_the_integration_checks(home, repo, monkeypatch) -> None:
    _install_hooks(home, at=_NOW - 20 * _DAY)
    from repowise.cli.commands.doctor_cmd._checks.context import CheckContext

    monkeypatch.setattr(integrations, "_distill_checks", lambda _p: [])
    monkeypatch.setattr(integrations, "_agent_target_checks", lambda _p: ([], False))
    monkeypatch.setattr(integrations, "_claude_registration_check", lambda _p: (None, False))
    monkeypatch.setattr(integrations, "_claude_desktop_entry_row", lambda: None)
    ctx = CheckContext(repo_path=repo, repowise_dir=repo / ".repowise", quick=True)
    ids = [c.id for c in integrations.integration_checks(ctx) if c is not None]
    assert "agent-hooks-firing" in ids


# --- SessionStart -----------------------------------------------------------


def _off_path(monkeypatch, which: str | None) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: which)


def test_session_start_says_distilling_is_off_when_repowise_is_off_path(
    home, monkeypatch
) -> None:
    _install_hooks(home, at=_NOW, rewrite=True)
    _off_path(monkeypatch, None)
    note = session_start._distill_off_path_note()
    assert note == "Command distilling is off: repowise is not on PATH."
    assert len(note.encode("utf-8")) <= 100


def test_session_start_is_silent_when_repowise_is_on_path(home, monkeypatch) -> None:
    _install_hooks(home, at=_NOW, rewrite=True)
    _off_path(monkeypatch, "/usr/bin/repowise")
    assert session_start._distill_off_path_note() == ""


def test_session_start_is_silent_without_the_rewrite_hook(home, monkeypatch) -> None:
    _install_hooks(home, at=_NOW, rewrite=False)
    _off_path(monkeypatch, None)
    assert session_start._distill_off_path_note() == ""


def test_the_sentence_rides_on_the_freshness_line(home, repo, monkeypatch) -> None:
    (repo / ".repowise" / "state.json").write_text(
        json.dumps({"last_sync_commit": "1" * 40}), encoding="utf-8"
    )
    monkeypatch.setattr(session_start, "_git_head", lambda _p: "1" * 40)
    monkeypatch.setattr(session_start, "_session_decision_block", lambda *_a: None)
    _install_hooks(home, at=_NOW, rewrite=True)
    _off_path(monkeypatch, None)
    out = session_start._handle_claude_session_start(str(repo))
    assert out is not None
    assert out.endswith("Codebase index is current (HEAD 11111111). " + session_start._DISTILL_OFF_PATH)
    _off_path(monkeypatch, "/usr/bin/repowise")
    assert "distilling" not in session_start._handle_claude_session_start(str(repo))


def test_an_unreadable_ledger_never_warns(home, repo) -> None:
    _install_hooks(home, at=_NOW - 20 * _DAY)
    for i in range(3):
        _session(repo, f"silent{i}", _NOW - (10 - i) * _DAY)
    db = repo / ".repowise" / "sessions" / "sessions.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"not a sqlite database at all" * 10)
    assert hook_ledger.ledger_session_ids(repo) is None
    row = integrations._hooks_firing_row(repo)
    assert row.level == "ok"
    assert "could not read" in row.detail


def test_runs_older_than_the_window_are_not_called_never(home, repo, monkeypatch) -> None:
    monkeypatch.setattr(integrations, "_RECENT_SESSIONS", 3)
    _install_hooks(home, at=_NOW - 40 * _DAY)
    _session(repo, "ran", _NOW - 30 * _DAY)
    _fired(repo, "ran")
    for i in range(3):
        _session(repo, f"silent{i}", _NOW - (10 - i) * _DAY)
    row = integrations._hooks_firing_row(repo)
    assert row.level == "warn"
    assert "in the last 3 sessions" in row.detail
    assert "any session" not in row.detail


def test_subagent_and_undated_transcripts_do_not_count(home, repo) -> None:
    _install_hooks(home, at=_NOW - 20 * _DAY)
    for i in range(3):
        _session(repo, f"agent-{i}", _NOW - (10 - i) * _DAY)
    directory = transcript_dir_for(repo.resolve())
    for i in range(3):
        undated = directory / f"undated{i}.jsonl"
        undated.write_text('{"type": "summary"}\n', encoding="utf-8")
    assert integrations._hooks_firing_row(repo).level == "ok"
