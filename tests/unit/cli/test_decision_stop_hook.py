"""The Stop prompt (``decisions.stop_prompt``): the gates, not the wording.

Off by default, silent and cheap when off; on, it blocks one stop a session,
never the stop that follows a block, never for a session that recorded a
decision or did no real work.
"""

from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from repowise.cli import decision_stop_hook
from repowise.cli.hook_ledger import _record_hook_run

_START = datetime(2026, 11, 27, 10, 0, 0, tzinfo=UTC)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".repowise").mkdir(parents=True)
    _config(root, "decisions:\n  stop_prompt: true\n")
    conn = sqlite3.connect(root / ".repowise" / "wiki.db")
    conn.execute(
        "CREATE TABLE decision_records (id TEXT PRIMARY KEY, source TEXT, created_at TEXT)"
    )
    conn.commit()
    conn.close()
    return root


def _config(root: Path, text: str) -> None:
    (root / ".repowise" / "config.yaml").write_text(text, encoding="utf-8")


def _transcript(root: Path, session: str) -> str:
    path = root.parent / f"{session}.jsonl"
    lines = [
        {"type": "summary"},
        {"type": "user", "timestamp": _START.strftime("%Y-%m-%dT%H:%M:%S.000Z")},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return str(path)


def _work(root: Path, session: str, tool: str = "Edit", calls: int = 1) -> None:
    for _ in range(calls):
        _record_hook_run(root, session, "PostToolUse", tool, emitted=False)


def _record(root: Path, source: str, at: datetime) -> None:
    conn = sqlite3.connect(root / ".repowise" / "wiki.db")
    conn.execute(
        "INSERT INTO decision_records VALUES (?, ?, ?)",
        (f"d{at.timestamp()}{source}", source, at.strftime("%Y-%m-%d %H:%M:%S.%f")),
    )
    conn.commit()
    conn.close()


def _stop(root: Path, session: str = "s1", **over) -> str | None:
    payload = {
        "hook_event_name": "Stop",
        "session_id": session,
        "cwd": str(root),
        "transcript_path": _transcript(root, session),
        "stop_hook_active": False,
    }
    payload.update(over)
    return decision_stop_hook.block_reason(payload)


# --- it fires ---------------------------------------------------------------


def test_it_blocks_a_session_that_edited_and_recorded_nothing(repo: Path) -> None:
    _work(repo, "s1")
    out = io.StringIO()
    stdin = io.StringIO(
        json.dumps(
            {
                "hook_event_name": "Stop",
                "session_id": "s1",
                "cwd": str(repo),
                "transcript_path": _transcript(repo, "s1"),
                "stop_hook_active": False,
            }
        )
    )
    decision_stop_hook.run(stdin, out)
    response = json.loads(out.getvalue())
    assert response["decision"] == "block"
    assert "repowise decision add" in response["reason"]
    assert "No decision to record" in response["reason"]


def test_it_blocks_once_a_session(repo: Path) -> None:
    _work(repo, "s1")
    assert _stop(repo) is not None
    assert _stop(repo) is None
    # Another session is asked on its own.
    _work(repo, "s2")
    assert _stop(repo, "s2") is not None


def test_enough_tool_calls_count_as_work_without_an_edit(repo: Path) -> None:
    _work(repo, "s1", "Read", decision_stop_hook.MIN_TOOL_CALLS - 1)
    assert _stop(repo) is None
    _work(repo, "s1", "Grep")
    assert _stop(repo) is not None


# --- it stays quiet ---------------------------------------------------------


def test_never_on_the_stop_that_follows_a_block(repo: Path) -> None:
    _work(repo, "s1")
    assert _stop(repo, stop_hook_active=True) is None
    # And that stop did not spend the session's ask.
    assert _stop(repo) is not None


def test_quiet_when_the_session_did_no_work(repo: Path) -> None:
    _work(repo, "s1", "Read", 3)
    assert _stop(repo) is None


def test_quiet_when_a_decision_was_recorded_this_session(repo: Path) -> None:
    _work(repo, "s1")
    _record(repo, "agent", _START + timedelta(minutes=5))
    assert _stop(repo) is None


def test_a_person_recording_by_hand_counts_too(repo: Path) -> None:
    _work(repo, "s1")
    _record(repo, "cli", _START + timedelta(minutes=5))
    assert _stop(repo) is None


def test_records_from_before_the_session_or_mined_ones_do_not_count(repo: Path) -> None:
    _work(repo, "s1")
    _record(repo, "agent", _START - timedelta(days=1))
    _record(repo, "pr", _START + timedelta(minutes=5))
    assert _stop(repo) is not None


def test_quiet_without_a_transcript_to_date_the_session(repo: Path) -> None:
    _work(repo, "s1")
    assert _stop(repo, transcript_path="") is None
    assert _stop(repo, transcript_path=str(repo / "missing.jsonl")) is None


def test_quiet_for_other_events_and_payloads(repo: Path) -> None:
    _work(repo, "s1")
    assert _stop(repo, hook_event_name="SubagentStop") is None
    assert _stop(repo, session_id="") is None
    assert _stop(repo, cwd=None) is None


# --- off --------------------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    [
        "decisions:\n  capture_prompt: true\n",
        "decisions:\n  stop_prompt: false\n",
        "decisions:\n  mode: off\n  stop_prompt: true\n",
        "decisions:\n  enabled: false\n  stop_prompt: true\n",
    ],
)
def test_off_unless_the_repository_turns_it_on(repo: Path, config: str) -> None:
    _config(repo, config)
    _work(repo, "s1")
    assert _stop(repo) is None


def test_off_writes_nothing_and_never_loads_the_augment_package(repo: Path) -> None:
    """The off path is a config read: no ledger, no state file, no heavy import."""
    _config(repo, "decisions:\n  capture_prompt: true\n")
    payload = json.dumps(
        {
            "hook_event_name": "Stop",
            "session_id": "s1",
            "cwd": str(repo),
            "transcript_path": "",
            "stop_hook_active": False,
        }
    )
    script = (
        "import io, sys\n"
        "from repowise.cli import decision_stop_hook as h\n"
        "out = io.StringIO()\n"
        f"h.run(io.StringIO({payload!r}), out)\n"
        "heavy = [m for m in sys.modules if m.startswith(('repowise.cli.commands', "
        "'repowise.core.analysis', 'click', 'sqlalchemy', 'yaml'))]\n"
        "print(repr(out.getvalue()), heavy)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60, check=True
    )
    assert result.stdout.strip() == "'' []"
    assert not (repo / ".repowise" / "sessions").exists()
    assert not (repo / ".repowise" / "hook-sessions").exists()


def test_the_registry_lists_it_off_by_default() -> None:
    from repowise.core.settings import registry

    setting = registry.get("decisions.stop_prompt")
    assert setting.default is False
    assert "once a session" in setting.description
