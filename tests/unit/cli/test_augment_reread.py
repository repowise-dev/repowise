"""Collapsing a re-read of unchanged bytes to a notice (``updatedToolOutput``).

The gates here are arithmetic, so the tests are about the arithmetic being
*applied* — that a changed file is served, that a different range is not
mistaken for a change, that a session cannot inherit another session's memory,
and that the escape hatch in the notice is true. A replacement surface whose
promise is false in the text is worse than one that never fires.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.cli.agent_adapters.claude_code import ClaudeCodeAdapter
from repowise.cli.commands.augment_cmd import _handle_post_tool_use
from repowise.cli.commands.augment_cmd.read_state import _load_session_state
from repowise.core.distill.budget import estimate_read_tokens

SESSION = "sess-reread"

#: Long enough that the notice is a real saving; the only gate is that the
#: notice is smaller than what it replaces.
_BODY = "\n".join(f"line {i} carrying enough text to be worth not repeating" for i in range(120))


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "hooks:\n  read_reread: true\n", encoding="utf-8"
    )
    (tmp_path / "a.py").write_text(_BODY, encoding="utf-8")
    monkeypatch.setenv("REPOWISE_HOOK_UPDATED_OUTPUT", "1")
    return tmp_path


def _read(repo: Path, *, content: str | None = None, session: str = SESSION, **window):
    """Fire the real PostToolUse Read handler and return its HookResult."""
    body = _BODY if content is None else content
    tool_input = {"file_path": str(repo / "a.py"), **window}
    return _handle_post_tool_use(
        "Read",
        tool_input,
        {
            "type": "text",
            "file": {
                "filePath": str(repo / "a.py"),
                "content": body,
                "numLines": body.count("\n") + 1,
                "startLine": window.get("offset", 1),
                "totalLines": _BODY.count("\n") + 1,
            },
        },
        str(repo),
        session_id=session,
    )


def _served(result) -> str | None:
    """The content the agent actually receives, when it was replaced."""
    if result.replacement is None:
        return None
    return result.replacement["file"]["content"]


class TestTheCollapse:
    def test_the_first_read_is_untouched(self, repo: Path) -> None:
        assert _read(repo).replacement is None

    def test_an_unchanged_re_read_is_collapsed(self, repo: Path) -> None:
        _read(repo)
        served = _served(_read(repo))
        assert served is not None, "identical bytes should not be sent twice"
        assert "Unchanged since you read it: a.py" in served
        assert "the whole file" in served, "the notice must name the range served before"

    def test_the_notice_names_no_tool_call_number(self, repo: Path) -> None:
        """The state counts only the reads and edits it tracks, not the agent's calls."""
        _read(repo)
        served = _served(_read(repo))
        assert "earlier in this session" in served
        assert "tool call" not in served

    def test_the_replacement_is_read_shaped_not_a_bare_string(self, repo: Path) -> None:
        """Read validates ``updatedToolOutput`` against its own schema; a bare
        string is rejected silently and the agent sees the original bytes."""
        _read(repo)
        payload = _read(repo).replacement
        assert isinstance(payload, dict)
        assert payload["file"]["filePath"] == str(repo / "a.py")
        assert payload["file"]["numLines"] == payload["file"]["content"].count("\n") + 1
        assert payload["file"]["totalLines"] == _BODY.count("\n") + 1

    def test_a_short_file_is_not_collapsed(self, repo: Path) -> None:
        """The notice would be bigger than the content. No threshold is tuned
        here — it is an exact comparison."""
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
        _read(repo, content="x = 1\n")
        assert _read(repo, content="x = 1\n").replacement is None


class TestWhenTheBytesDiffer:
    def test_a_changed_file_is_served_not_collapsed(self, repo: Path) -> None:
        _read(repo)
        changed = _BODY + "\nsomething else entirely"
        (repo / "a.py").write_text(changed, encoding="utf-8")
        result = _read(repo, content=changed)
        assert result.replacement is None, "different bytes must always be served"

    def test_and_the_agent_is_told_what_changed(self, repo: Path) -> None:
        """More valuable than the bytes: nothing else in the session can learn
        that a file moved under it without an Edit of ours."""
        _read(repo)
        changed = _BODY + "\nsomething else entirely"
        (repo / "a.py").write_text(changed, encoding="utf-8")
        context = _read(repo, content=changed).context or ""
        assert "a.py changed on disk since you read it" in context
        assert "not through an Edit in this session" in context

    def test_the_change_notice_fires_once(self, repo: Path) -> None:
        _read(repo)
        changed = _BODY + "\nsomething else entirely"
        (repo / "a.py").write_text(changed, encoding="utf-8")
        assert "changed on disk" in (_read(repo, content=changed).context or "")
        again = _read(repo, content=changed + "\nand again")
        assert "changed on disk" not in (again.context or "")

    def test_a_different_range_is_not_reported_as_a_change(self, repo: Path) -> None:
        """Different offsets legitimately return different bytes. Calling that
        a change would be the judgment this surface exists to avoid making."""
        _read(repo)
        result = _read(repo, content="line 5\nline 6", offset=5, limit=2)
        assert result.replacement is None
        assert "changed on disk" not in (result.context or "")


class TestTheEscapeHatch:
    def test_never_collapsed_twice_in_a_row(self, repo: Path) -> None:
        """A compaction can take the earlier copy with it, and that is not
        detectable from a hook. Reading again always returns the content."""
        _read(repo)
        assert _read(repo).replacement is not None
        assert _read(repo).replacement is None, "the notice promises the bytes come back"
        assert _read(repo).replacement is not None

    def test_the_notice_states_that_hatch(self, repo: Path) -> None:
        _read(repo)
        assert "Read it again and you get the content" in _served(_read(repo))


class TestAgainstTheOtherReplacingSurface:
    """A Read some *other* surface replaced delivered bytes the agent never got.

    Both surfaces on at once is the configuration one init consent produces, so
    this is the default arrangement, not an exotic one.
    """

    @pytest.fixture
    def both_on(self, repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        (repo / ".repowise" / "config.yaml").write_text(
            "hooks:\n  read_reread: true\n  read_skeleton: true\n", encoding="utf-8"
        )
        # Stand in for the indexed skeleton so the surface fires without an
        # index; what is under test is the interaction, not the skeleton.
        from repowise.cli.commands.augment_cmd import read_skeleton
        from repowise.cli.commands.augment_cmd.replacement import Offer

        monkeypatch.setattr(
            read_skeleton,
            "skeleton_replacement",
            lambda repo_path, rel, **kw: Offer(
                key=rel,
                text=f"[repowise] Skeleton of {rel}.\ndef a(): ...",
                raw_tokens=10_000,
                new_tokens=20,
                category="skeleton_served",
                filter_name="read_skeleton",
            ),
        )
        return repo

    def test_a_skeleton_is_never_followed_by_you_already_have_this(
        self, both_on: Path
    ) -> None:
        """The collapse must not claim bytes a skeleton stood in for.

        Otherwise the second Read says "you were served the whole file" to an
        agent that was served signatures, and the skeleton's own promise —
        read it again and you get it whole — is broken by a third read.
        """
        assert "Skeleton of" in _served(_read(both_on))
        second = _read(both_on)
        assert second.replacement is None, (
            "the Read after a skeleton must return the file, not a notice about it"
        )

    def test_and_that_read_is_not_reported_as_an_external_change(
        self, both_on: Path
    ) -> None:
        """The other half: the skeleton's digest differs from the file's, and
        treating that as a change would invent one."""
        _read(both_on)
        assert "changed on disk" not in (_read(both_on).context or "")

    def test_a_changed_file_is_not_served_as_a_skeleton(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The change notice says the content below is current. A skeleton
        would make that half true at the moment the agent most needs a body.

        The first Read has to deliver real bytes for there to be anything to
        compare against, so the skeleton is turned on only after it — which is
        also the shape of a repo that enables the surface mid-session.
        """
        _read(repo)
        changed = _BODY + "\nsomething else entirely"
        (repo / "a.py").write_text(changed, encoding="utf-8")

        (repo / ".repowise" / "config.yaml").write_text(
            "hooks:\n  read_reread: true\n  read_skeleton: true\n", encoding="utf-8"
        )
        from repowise.cli.commands.augment_cmd import read_skeleton
        from repowise.cli.commands.augment_cmd.replacement import Offer

        monkeypatch.setattr(
            read_skeleton,
            "skeleton_replacement",
            lambda repo_path, rel, **kw: Offer(
                key=rel,
                text=f"[repowise] Skeleton of {rel}.",
                raw_tokens=10_000,
                new_tokens=20,
                category="skeleton_served",
                filter_name="read_skeleton",
            ),
        )
        result = _read(repo, content=changed)
        assert "changed on disk" in (result.context or "")
        assert result.replacement is None, "a file that just changed is served whole"


class TestSessionIsolation:
    def test_a_second_session_does_not_inherit_the_first(self, repo: Path) -> None:
        """A subagent runs under its own session id and never received the
        parent's bytes, so it must not be told it already has them."""
        _read(repo)
        _read(repo)
        assert _read(repo, session="subagent-1").replacement is None

    def test_each_session_keeps_its_own_state_file(self, repo: Path) -> None:
        _read(repo)
        _read(repo, session="subagent-1")
        files = list((repo / ".repowise" / "hook-sessions").glob("*.json"))
        assert len(files) == 2
        for path in files:
            assert json.loads(path.read_text(encoding="utf-8"))["session_id"] in (
                SESSION,
                "subagent-1",
            )


class TestGates:
    def test_off_by_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".repowise").mkdir()
        (tmp_path / "a.py").write_text(_BODY, encoding="utf-8")
        monkeypatch.setenv("REPOWISE_HOOK_UPDATED_OUTPUT", "1")
        _read(tmp_path)
        assert _read(tmp_path).replacement is None

    def test_a_client_that_cannot_replace_is_never_handed_one(self, repo: Path) -> None:
        _read(repo)
        result = _handle_post_tool_use(
            "Read",
            {"file_path": str(repo / "a.py")},
            {"type": "text", "file": {"filePath": str(repo / "a.py"), "content": _BODY}},
            str(repo),
            client="codex",
            session_id=SESSION,
        )
        assert result.replacement is None

    def test_an_old_client_build_is_never_handed_one(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _read(repo)
        monkeypatch.setattr(ClaudeCodeAdapter, "supports_updated_output", lambda self: False)
        assert _read(repo).replacement is None

    def test_a_verification_read_after_an_edit_is_never_collapsed(self, repo: Path) -> None:
        """Re-reading what you just edited needs fidelity, not a pointer."""
        _read(repo)
        _handle_post_tool_use(
            "Edit", {"file_path": str(repo / "a.py")}, {}, str(repo), session_id=SESSION
        )
        result = _read(repo)
        assert result.replacement is None
        assert "stale" in (result.context or "")


class TestLedger:
    def test_a_collapse_is_recorded_and_a_saving_billed(self, repo: Path) -> None:
        import sqlite3

        omissions = repo / ".repowise" / "omissions"
        omissions.mkdir(parents=True)
        con = sqlite3.connect(omissions / "omissions.db")
        con.execute(
            "CREATE TABLE savings (created_at REAL, filter TEXT, source TEXT, "
            "command TEXT, raw_tokens INTEGER, distilled_tokens INTEGER)"
        )
        con.commit()
        con.close()

        _read(repo)
        result = _read(repo)
        assert result.replacement is not None
        assert result.on_emitted is not None
        result.on_emitted()

        con = sqlite3.connect(omissions / "omissions.db")
        try:
            rows = con.execute(
                "SELECT filter, source, command, raw_tokens, distilled_tokens FROM savings"
            ).fetchall()
        finally:
            con.close()
        # Claude Code numbers every line of the Read the collapse replaced.
        raw = estimate_read_tokens(_BODY, numbered=True)
        assert rows == [("read_reread", "hook-read", "a.py", raw, pytest.approx(rows[0][4]))]
        assert rows[0][4] < rows[0][3], "a collapse that saved nothing is not a saving"

    def test_the_state_records_what_was_served(self, repo: Path) -> None:
        _read(repo)
        meta = _load_session_state(repo, SESSION)["read_meta"]["a.py"]
        assert meta["off"] is None and meta["lim"] is None
        assert meta["seq"] == 1
        assert len(meta["h"]) == 12


class TestSubagentsKeepTheirOwnState:
    """A subagent's payload carries its parent's session id plus an ``agent_id``.

    It never received the parent's bytes, so a parent Read must not collapse
    the subagent's first Read of the same file, nor the other way round.
    """

    @pytest.fixture(autouse=True)
    def _main_thread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from repowise.cli.commands.augment_cmd import read_state

        monkeypatch.setattr(read_state, "_agent_id", "")

    @staticmethod
    def _as(agent: str | None) -> None:
        from repowise.cli.commands.augment_cmd.read_state import bind_agent

        bind_agent({} if agent is None else {"agent_id": agent})

    def test_a_parent_read_does_not_collapse_the_subagents_first_read(self, repo: Path) -> None:
        self._as(None)
        _read(repo)
        self._as("agent-1")
        assert _read(repo).replacement is None

    def test_a_subagent_read_does_not_collapse_the_parents_first_read(self, repo: Path) -> None:
        self._as("agent-1")
        _read(repo)
        self._as(None)
        assert _read(repo).replacement is None

    def test_two_subagents_do_not_share_state(self, repo: Path) -> None:
        self._as("agent-1")
        _read(repo)
        self._as("agent-2")
        assert _read(repo).replacement is None

    def test_a_subagent_still_collapses_its_own_re_read(self, repo: Path) -> None:
        self._as("agent-1")
        _read(repo)
        assert _served(_read(repo)) is not None

    @staticmethod
    def _edit(repo: Path, body: str) -> None:
        (repo / "a.py").write_text(body, encoding="utf-8")
        _handle_post_tool_use(
            "Edit", {"file_path": str(repo / "a.py")}, {}, str(repo), session_id=SESSION
        )

    @pytest.mark.parametrize(("reader", "editor"), [(None, "agent-1"), ("agent-1", None)])
    def test_an_edit_by_another_window_is_stale_not_an_outside_change(
        self, repo: Path, reader: str | None, editor: str | None
    ) -> None:
        changed = _BODY + "\nedited"
        self._as(reader)
        _read(repo)
        self._as(editor)
        self._edit(repo, changed)
        self._as(reader)
        context = _read(repo, content=changed).context or ""
        assert "stale" in context
        assert "changed on disk" not in context

    def test_an_edit_by_another_window_after_a_skeleton_is_stale(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A replaced Read keeps no content record, but its order still counts."""
        from repowise.cli.commands.augment_cmd import read_skeleton
        from repowise.cli.commands.augment_cmd.replacement import Offer

        (repo / ".repowise" / "config.yaml").write_text(
            "hooks:\n  read_skeleton: true\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            read_skeleton,
            "skeleton_replacement",
            lambda repo_path, rel, **kw: Offer(
                key=rel,
                text=f"[repowise] Skeleton of {rel}.\ndef a(): ...",
                raw_tokens=10_000,
                new_tokens=20,
                category="skeleton_served",
                filter_name="read_skeleton",
            ),
        )
        self._as(None)
        assert "Skeleton of" in (_served(_read(repo)) or "")
        changed = _BODY + "\nedited"
        self._as("agent-1")
        self._edit(repo, changed)
        self._as(None)
        assert "stale" in (_read(repo, content=changed).context or "")

    def test_an_outside_write_after_a_subagent_edit_is_still_an_outside_change(
        self, repo: Path
    ) -> None:
        import os

        self._as(None)
        _read(repo)
        self._as("agent-1")
        self._edit(repo, _BODY + "\nedited")
        outside = _BODY + "\nwritten by something else"
        (repo / "a.py").write_text(outside, encoding="utf-8")
        stat = (repo / "a.py").stat()
        os.utime(repo / "a.py", ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        self._as(None)
        assert "changed on disk" in (_read(repo, content=outside).context or "")


@pytest.mark.parametrize(
    ("client", "payload"),
    [
        # Claude Code: agent_id only inside a subagent, absent on the main thread.
        (None, {"hook_event_name": "PostToolUse", "tool_name": "Read", "agent_id": "a1"}),
        # Codex: the same optional field on its PostToolUse input.
        (
            "codex",
            {"hook_event_name": "PostToolUse", "tool_name": "Bash", "agent_id": "a1", "turn_id": "t"},
        ),
        (None, {"hook_event_name": "PostToolUse", "tool_name": "Read"}),
    ],
)
def test_the_hook_binds_the_payloads_agent(
    client: str | None, payload: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    from repowise.cli.commands.augment_cmd import command, read_state

    monkeypatch.setattr(read_state, "_agent_id", "stale")
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({**payload, "session_id": SESSION, "cwd": str(tmp_path)})),
    )
    command._run_augment(client=client)
    assert read_state._agent_id == payload.get("agent_id", "")
    path = read_state._session_state_path(tmp_path, SESSION)
    read_state._agent_id = ""
    main_path = read_state._session_state_path(tmp_path, SESSION)
    assert (path == main_path) is ("agent_id" not in payload)
