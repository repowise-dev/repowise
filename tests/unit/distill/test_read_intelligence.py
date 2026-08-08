"""Read-intelligence PostToolUse behaviors: stale reads, and what was retired.

Exercises the augment handlers directly (the `_handle_post_tool_use`
dispatch layer), below `_emit_response`'s cross-process dedup, so the
per-session state file is the only rate limiter under test.

Scope: the *additional context* half of the Read surface — what the hook says
alongside a tool result. The half that replaces a tool result outright lives
in `tests/unit/cli/test_augment_read_skeleton.py`; the helpers below read
`HookResult.context` so a replacement can never be mistaken for a notice here.
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
    """Default size clears the replacement's full-file token floor (~3k tokens)."""
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"x{n} = {n}  # padding padding" for n in range(lines)) + "\n")
    return path


def _post(tool: str, **kwargs) -> str | None:
    """Dispatch a PostToolUse event and return only what the agent is *told*.

    Every assertion in this module is about ``additionalContext``. Unwrapping
    here keeps a stray ``updatedToolOutput`` from reading as a notice.
    """
    return _handle_post_tool_use(tool, **kwargs).context


def _read_event(repo: Path, rel: str, num_lines: int = 150, session: str = SESSION):
    return _post(
        "Read",
        tool_input={"file_path": str(repo / rel)},
        tool_output={"file": {"numLines": num_lines}},
        cwd=str(repo),
        session_id=session,
    )


def _edit_event(repo: Path, rel: str, tool: str = "Edit", session: str = SESSION):
    return _post(
        tool,
        tool_input={"file_path": str(repo / rel)},
        tool_output={"success": True},
        cwd=str(repo),
        session_id=session,
    )


class TestRetiredSkeletonNudge:
    """The skeleton nudge was deleted; these keep it deleted.

    It was the loudest surface in the system and it never earned a number.
    Three readings were replayed over 516 firings before it was touched: a
    structure call on any file followed 11.4% of nudges against an 11.9%
    unconditioned base rate, ``get_context`` on a not-yet-read file 2.9%
    against 3.4%, and read as compliance — did the agent stop reading large
    indexed files whole — 54.8% re-offended inside the window. A session's
    first nudge re-offended at 57.1% and its later ones at 53.4%, so being
    told again changed nothing. What it asked for is now done by the
    replacement rather than said.
    """

    def test_a_big_read_of_an_indexed_file_says_nothing(self, repo: Path) -> None:
        _write_big_file(repo, "src/big.py")
        _index_file(repo, "src/big.py", [(10, 60), (70, 150), (160, 195)])
        assert _read_event(repo, "src/big.py") is None

    def test_no_pointer_survives_on_any_read_of_that_file(self, repo: Path) -> None:
        """Including the second and a fresh session, which used to re-fire."""
        _write_big_file(repo, "src/big.py")
        _index_file(repo, "src/big.py", [(10, 60), (70, 150), (160, 195)])
        assert _read_event(repo, "src/big.py") is None
        assert _read_event(repo, "src/big.py") is None
        assert _read_event(repo, "src/big.py", session="session-2") is None

    def test_a_read_outside_any_repowise_repo_is_left_alone(self, tmp_path: Path) -> None:
        """`_handle_read_post`'s own early return, which the nudge tests used to
        be the only cover for. It guards every Read notice, not just the one
        that went."""
        plain = tmp_path / "plain"
        _write_big_file(plain, "src/big.py")
        assert (
            _post(
                "Read",
                tool_input={"file_path": str(plain / "src/big.py")},
                tool_output={"file": {"numLines": 150}},
                cwd=str(plain),
                session_id=SESSION,
            )
            is None
        )

    def test_the_stale_read_notice_is_unaffected(self, repo: Path) -> None:
        """The one Read notice that survived, asserted on the nudge's own shape."""
        _write_big_file(repo, "src/big.py")
        _index_file(repo, "src/big.py", [(10, 60), (70, 150), (160, 195)])
        _read_event(repo, "src/big.py")
        _edit_event(repo, "src/big.py")
        notice = _read_event(repo, "src/big.py")
        assert notice is not None and "stale" in notice
        assert 'include=["skeleton"]' not in notice


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
        result = _post(
            "Edit",
            tool_input={"file_path": str(outside)},
            tool_output={"success": True},
            cwd=str(repo),
            session_id=SESSION,
        )
        assert result is None
        state = _load_session_state(repo, SESSION)
        assert state["edits"] == {}


class TestRetiredRereadNotice:
    """The re-read notice was deleted; these keep it deleted.

    It scored 100% "respected" only because agents rarely read the same file a
    third time — it was measuring the base rate. The case it argued is now
    handled by serving a skeleton instead of saying so.
    """

    def test_a_full_reread_of_an_unchanged_file_says_nothing(self, repo: Path) -> None:
        _write_big_file(repo, "a.py", lines=200)
        for _ in range(3):
            assert _read_event(repo, "a.py", num_lines=150) is None

    def test_an_edit_between_reads_still_flags_staleness(self, repo: Path) -> None:
        """The notice that survived, and the one that did not, in one place."""
        _write_big_file(repo, "a.py", lines=200)
        _read_event(repo, "a.py", num_lines=150)
        _edit_event(repo, "a.py")
        notice = _read_event(repo, "a.py", num_lines=150)
        assert notice is not None and "stale" in notice
        assert "already read" not in notice


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
        result = _post(
            "Edit",
            tool_input={"file_path": str(repo / "a.py")},
            tool_output={"success": True},
            cwd=str(repo),
            client="codex",
            session_id=SESSION,
        )
        assert result is not None and "Files were edited" in result
        state = _load_session_state(repo, SESSION)
        assert "a.py" in state["edits"]
