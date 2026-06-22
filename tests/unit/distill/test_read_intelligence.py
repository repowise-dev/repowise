"""Read-intelligence PostToolUse behaviors: skeleton nudges + stale reads.

Exercises the augment handlers directly (the `_handle_post_tool_use`
dispatch layer), below `_emit_response`'s cross-process dedup, so the
per-session state file is the only rate limiter under test.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from repowise.cli.commands.augment_cmd import (
    _handle_post_tool_use,
    _load_session_state,
    _session_state_path,
)

SESSION = "session-abc"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".repowise").mkdir()
    return tmp_path


def _index_file(repo: Path, rel: str, bounds: list[tuple[int, int]]) -> None:
    """Minimal wiki.db carrying just the columns the hook path queries."""
    db = repo / ".repowise" / "wiki.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE IF NOT EXISTS wiki_symbols "
        "(file_path TEXT, start_line INTEGER, end_line INTEGER)"
    )
    con.executemany(
        "INSERT INTO wiki_symbols VALUES (?, ?, ?)",
        [(rel, s, e) for s, e in bounds],
    )
    con.commit()
    con.close()


def _write_big_file(repo: Path, rel: str, lines: int = 600) -> Path:
    """Default size clears the nudge's full-file token floor (~3k tokens)."""
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"x{n} = {n}  # padding padding" for n in range(lines)) + "\n")
    return path


def _read_event(repo: Path, rel: str, num_lines: int = 150, session: str = SESSION):
    return _handle_post_tool_use(
        "Read",
        {"file_path": str(repo / rel)},
        {"file": {"numLines": num_lines}},
        str(repo),
        session_id=session,
    )


def _edit_event(repo: Path, rel: str, tool: str = "Edit", session: str = SESSION):
    return _handle_post_tool_use(
        tool,
        {"file_path": str(repo / rel)},
        {"success": True},
        str(repo),
        session_id=session,
    )


class TestSkeletonNudge:
    def test_fires_for_big_read_of_indexed_file(self, repo: Path) -> None:
        _write_big_file(repo, "src/big.py")
        _index_file(repo, "src/big.py", [(10, 60), (70, 150), (160, 195)])

        result = _read_event(repo, "src/big.py")
        assert result is not None
        assert 'include=["skeleton"]' in result
        assert "src/big.py" in result
        assert "tokens" in result

    def test_fires_exactly_once_per_file_per_session(self, repo: Path) -> None:
        _write_big_file(repo, "src/big.py")
        _index_file(repo, "src/big.py", [(10, 60), (70, 150), (160, 195)])

        first = _read_event(repo, "src/big.py")
        assert first is not None and 'include=["skeleton"]' in first
        # Second read of the same big file surfaces a re-read notice (content
        # already in context), not a second skeleton pointer.
        second = _read_event(repo, "src/big.py")
        assert second is not None and 'include=["skeleton"]' not in second
        assert "already read" in second
        # Third read: both the skeleton and re-read notices are once-per-file.
        assert _read_event(repo, "src/big.py") is None
        # A new session resets the claim — skeleton pointer fires again.
        third_session = _read_event(repo, "src/big.py", session="session-2")
        assert third_session is not None and 'include=["skeleton"]' in third_session

    def test_silent_below_line_threshold(self, repo: Path) -> None:
        _write_big_file(repo, "src/big.py")
        _index_file(repo, "src/big.py", [(10, 60), (70, 150)])
        assert _read_event(repo, "src/big.py", num_lines=40) is None

    def test_silent_without_wiki_db(self, repo: Path) -> None:
        _write_big_file(repo, "src/big.py")
        assert _read_event(repo, "src/big.py") is None

    def test_silent_when_file_not_indexed(self, repo: Path) -> None:
        _write_big_file(repo, "src/big.py")
        _index_file(repo, "src/other.py", [(1, 50)])
        assert _read_event(repo, "src/big.py") is None

    def test_silent_outside_any_repowise_repo(self, tmp_path: Path) -> None:
        plain = tmp_path / "plain"
        _write_big_file(plain, "src/big.py")
        result = _handle_post_tool_use(
            "Read",
            {"file_path": str(plain / "src/big.py")},
            {"file": {"numLines": 150}},
            str(plain),
            session_id=SESSION,
        )
        assert result is None

    def test_silent_for_tiny_file(self, repo: Path) -> None:
        # 150 reported lines but the on-disk file is too small to matter.
        path = repo / "src" / "small.py"
        path.parent.mkdir(parents=True)
        path.write_text("x = 1\n" * 30)
        _index_file(repo, "src/small.py", [(1, 30)])
        assert _read_event(repo, "src/small.py") is None

    def test_silent_below_token_floor(self, repo: Path) -> None:
        # The live noise case: a ~1.5k-token file with a perfectly valid
        # skeleton (~600 tokens saved) is a hint nobody should act on.
        _write_big_file(repo, "src/mid.py", lines=200)
        _index_file(repo, "src/mid.py", [(10, 60), (70, 150), (160, 195)])
        assert _read_event(repo, "src/mid.py") is None

    def test_fires_above_token_floor_with_real_savings(self, repo: Path) -> None:
        # Same shape as the noise case, just genuinely large: ≥3k full-file
        # tokens with ≥1.5k estimated savings.
        _write_big_file(repo, "src/big.py", lines=600)
        _index_file(repo, "src/big.py", [(10, 60), (70, 150), (160, 195)])
        result = _read_event(repo, "src/big.py")
        assert result is not None and 'include=["skeleton"]' in result


class TestStaleReadNotice:
    def test_edit_then_reread_flags_staleness(self, repo: Path) -> None:
        _write_big_file(repo, "a.py", lines=10)
        assert _read_event(repo, "a.py", num_lines=10) is None  # first read
        assert _edit_event(repo, "a.py") is None  # records, says nothing
        notice = _read_event(repo, "a.py", num_lines=10)
        assert notice is not None
        assert "a.py" in notice and "stale" in notice

    def test_write_tool_also_counts_as_edit(self, repo: Path) -> None:
        _write_big_file(repo, "a.py", lines=10)
        _read_event(repo, "a.py", num_lines=10)
        _edit_event(repo, "a.py", tool="Write")
        assert "stale" in (_read_event(repo, "a.py", num_lines=10) or "")

    def test_notice_is_once_per_file_per_session(self, repo: Path) -> None:
        _write_big_file(repo, "a.py", lines=10)
        _read_event(repo, "a.py", num_lines=10)
        _edit_event(repo, "a.py")
        assert _read_event(repo, "a.py", num_lines=10) is not None
        _edit_event(repo, "a.py")
        assert _read_event(repo, "a.py", num_lines=10) is None

    def test_no_notice_without_intervening_edit(self, repo: Path) -> None:
        _write_big_file(repo, "a.py", lines=10)
        assert _read_event(repo, "a.py", num_lines=10) is None
        assert _read_event(repo, "a.py", num_lines=10) is None

    def test_no_notice_when_first_read_follows_edit(self, repo: Path) -> None:
        # The agent never saw pre-edit content — nothing is stale for it.
        _write_big_file(repo, "a.py", lines=10)
        _edit_event(repo, "a.py")
        assert _read_event(repo, "a.py", num_lines=10) is None

    def test_per_file_isolation(self, repo: Path) -> None:
        _write_big_file(repo, "a.py", lines=10)
        _write_big_file(repo, "b.py", lines=10)
        _read_event(repo, "a.py", num_lines=10)
        _edit_event(repo, "b.py")  # different file
        assert _read_event(repo, "a.py", num_lines=10) is None

    def test_new_session_forgets_history(self, repo: Path) -> None:
        _write_big_file(repo, "a.py", lines=10)
        _read_event(repo, "a.py", num_lines=10)
        _edit_event(repo, "a.py")
        assert _read_event(repo, "a.py", num_lines=10, session="other") is None

    def test_edit_outside_repo_records_nothing(self, repo: Path, tmp_path: Path) -> None:
        outside = tmp_path.parent / "elsewhere.py"
        result = _handle_post_tool_use(
            "Edit",
            {"file_path": str(outside)},
            {"success": True},
            str(repo),
            session_id=SESSION,
        )
        assert result is None
        state = _load_session_state(repo, SESSION)
        assert state["edits"] == {}


class TestRereadNotice:
    def test_full_reread_of_unchanged_file_is_flagged(self, repo: Path) -> None:
        _write_big_file(repo, "a.py", lines=200)
        assert _read_event(repo, "a.py", num_lines=150) is None  # first read
        notice = _read_event(repo, "a.py", num_lines=150)
        assert notice is not None
        assert "a.py" in notice and "already read" in notice
        assert "get_symbol" in notice

    def test_notice_is_once_per_file_per_session(self, repo: Path) -> None:
        _write_big_file(repo, "a.py", lines=200)
        _read_event(repo, "a.py", num_lines=150)
        assert _read_event(repo, "a.py", num_lines=150) is not None
        assert _read_event(repo, "a.py", num_lines=150) is None

    def test_partial_reread_is_not_flagged(self, repo: Path) -> None:
        # A targeted range re-read is the behavior we recommend; never nag it.
        _write_big_file(repo, "a.py", lines=200)
        _read_event(repo, "a.py", num_lines=150)
        result = _handle_post_tool_use(
            "Read",
            {"file_path": str(repo / "a.py"), "offset": 40, "limit": 30},
            {"file": {"numLines": 30}},
            str(repo),
            session_id=SESSION,
        )
        assert result is None

    def test_small_file_reread_is_not_flagged(self, repo: Path) -> None:
        _write_big_file(repo, "a.py", lines=10)
        assert _read_event(repo, "a.py", num_lines=10) is None
        assert _read_event(repo, "a.py", num_lines=10) is None

    def test_edit_between_reads_is_stale_not_reread(self, repo: Path) -> None:
        _write_big_file(repo, "a.py", lines=200)
        _read_event(repo, "a.py", num_lines=150)
        _edit_event(repo, "a.py")
        notice = _read_event(repo, "a.py", num_lines=150)
        assert notice is not None and "stale" in notice
        assert "already read" not in notice

    def test_new_session_forgets_history(self, repo: Path) -> None:
        _write_big_file(repo, "a.py", lines=200)
        _read_event(repo, "a.py", num_lines=150)
        assert _read_event(repo, "a.py", num_lines=150, session="other") is None


class TestSessionState:
    def test_state_file_lives_under_repowise(self, repo: Path) -> None:
        _write_big_file(repo, "a.py", lines=10)
        _read_event(repo, "a.py", num_lines=10)
        assert _session_state_path(repo).exists()
        state = json.loads(_session_state_path(repo).read_text(encoding="utf-8"))
        assert state["session_id"] == SESSION
        assert "a.py" in state["reads"]

    def test_corrupt_state_file_is_replaced_not_fatal(self, repo: Path) -> None:
        _session_state_path(repo).write_text("{not json", encoding="utf-8")
        _write_big_file(repo, "a.py", lines=10)
        assert _read_event(repo, "a.py", num_lines=10) is None
        state = _load_session_state(repo, SESSION)
        assert "a.py" in state["reads"]

    def test_state_trims_unbounded_growth(self, repo: Path) -> None:
        from repowise.cli.commands.augment_cmd import _save_session_state

        state = {
            "session_id": SESSION,
            "reads": {f"f{i}.py": float(i) for i in range(600)},
            "edits": {},
            "nudged": [],
            "stale_notified": [],
        }
        _save_session_state(repo, state)
        saved = json.loads(_session_state_path(repo).read_text(encoding="utf-8"))
        assert len(saved["reads"]) == 400
        # Most recent timestamps survive the trim.
        assert "f599.py" in saved["reads"]
        assert "f0.py" not in saved["reads"]

    def test_codex_edit_banner_still_fires_and_records(self, repo: Path) -> None:
        (repo / ".repowise" / "state.json").write_text("{}", encoding="utf-8")
        result = _handle_post_tool_use(
            "Edit",
            {"file_path": str(repo / "a.py")},
            {"success": True},
            str(repo),
            client="codex",
            session_id=SESSION,
        )
        assert result is not None and "Files were edited" in result
        state = _load_session_state(repo, SESSION)
        assert "a.py" in state["edits"]
