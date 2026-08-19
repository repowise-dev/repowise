"""The PreToolUse rewrite hook counting itself.

Its firing rate is not observable any other way: an ``updatedInput`` rewrite
never appears in a transcript and neither does a passthrough, so before these
rows the busiest hook surface reported nothing at all and the reason a command
was let through was inference. These tests pin that the counters are written,
that they aggregate rather than dedup, and that a bail reason is specific
enough to be worth having.
"""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import pytest

from repowise.cli import rewrite_hook
from repowise.cli.hook_ledger import BAILED, REWRITTEN

SESSION = "sess-rewrite"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".repowise").mkdir()
    # One ledger connection is cached per repo path for the life of the
    # process; the fixture gives each test its own path, so nothing leaks.
    monkeypatch.setattr(rewrite_hook, "_POSIX_HOST", True)
    return tmp_path


def _fire(repo: Path, command: str, monkeypatch: pytest.MonkeyPatch, tool: str = "Bash") -> None:
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": {"command": command},
            "cwd": str(repo),
            "session_id": SESSION,
        }
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    monkeypatch.setattr("sys.argv", ["repowise-rewrite"])
    with pytest.raises(SystemExit):
        rewrite_hook.main()


def _rows(repo: Path) -> dict[tuple[str, str], int]:
    db = repo / ".repowise" / "sessions" / "sessions.db"
    con = sqlite3.connect(db)
    try:
        return {
            (outcome, reason): calls
            for outcome, reason, calls in con.execute(
                "SELECT outcome, reason, calls FROM rewrite_runs"
            )
        }
    finally:
        con.close()


def test_a_rewrite_is_counted_under_its_family(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fire(repo, "pytest -q", monkeypatch)
    assert _rows(repo) == {(REWRITTEN, "test_output"): 1}


def test_an_unrecognized_command_is_counted_as_such(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fire(repo, "./deploy.sh --prod", monkeypatch)
    assert _rows(repo) == {(BAILED, rewrite_hook.BAIL_UNRECOGNIZED): 1}


def test_a_shape_bail_is_told_apart_from_a_family_bail(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The distribution this surface exists to measure: shape versus family."""
    _fire(repo, "pytest -q > results.txt", monkeypatch)
    _fire(repo, "./deploy.sh", monkeypatch)
    rows = _rows(repo)
    assert rows[(BAILED, rewrite_hook.BAIL_SHAPE)] == 1
    assert rows[(BAILED, rewrite_hook.BAIL_UNRECOGNIZED)] == 1


def test_counts_aggregate_rather_than_dedup(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An INSERT-OR-IGNORE would answer "one of each" and measure nothing."""
    for _ in range(3):
        _fire(repo, "./deploy.sh", monkeypatch)
    assert _rows(repo)[(BAILED, rewrite_hook.BAIL_UNRECOGNIZED)] == 3


def test_a_disabled_repo_says_so_rather_than_looking_unrecognized(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / ".repowise" / "config.yaml").write_text(
        "distill:\n  commands:\n    enabled: false\n", encoding="utf-8"
    )
    _fire(repo, "pytest -q", monkeypatch)
    assert _rows(repo) == {(BAILED, rewrite_hook.BAIL_DISABLED): 1}


def test_a_family_turned_off_is_its_own_reason(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / ".repowise" / "config.yaml").write_text(
        "distill:\n  commands:\n    families:\n      test_output: off\n", encoding="utf-8"
    )
    _fire(repo, "pytest -q", monkeypatch)
    assert _rows(repo) == {(BAILED, rewrite_hook.BAIL_FAMILY_OFF): 1}


def test_rows_carry_the_emitting_build(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without it, two installs emitting into one repo are indistinguishable."""
    from repowise.cli.hook_ledger import emitting_build

    _fire(repo, "pytest -q", monkeypatch)
    con = sqlite3.connect(repo / ".repowise" / "sessions" / "sessions.db")
    try:
        assert con.execute("SELECT build FROM rewrite_runs").fetchone()[0] == emitting_build()
    finally:
        con.close()


def test_nothing_is_written_outside_a_repowise_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hook is installed globally. A directory that never opted in gets no
    sidecar, which is also why those commands can never be counted."""
    monkeypatch.setattr(rewrite_hook, "_POSIX_HOST", True)
    _fire(tmp_path, "pytest -q", monkeypatch)
    assert not (tmp_path / ".repowise").exists()


def test_a_payload_with_no_session_id_writes_nothing(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row nothing can be attributed to is not worth the write."""
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest -q"},
            "cwd": str(repo),
        }
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    monkeypatch.setattr("sys.argv", ["repowise-rewrite"])
    with pytest.raises(SystemExit):
        rewrite_hook.main()
    assert not (repo / ".repowise" / "sessions").exists()
