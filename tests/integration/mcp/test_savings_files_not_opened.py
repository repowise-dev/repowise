"""An MCP call credits the files its counterfactual sized as a Read, and nothing else.

The sizes are the ones the counterfactual already measured, so recording the
files adds no file or index access. A failure in that bookkeeping never
changes the reply.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from repowise.core.distill.store import OmissionStore
from repowise.server.mcp_server import _state, tool_middleware
from repowise.server.mcp_server._savings import declare_replaced
from repowise.server.mcp_server._savings.reads import file_read_tokens


def _repo_db(repo: Path) -> Path:
    return repo / ".repowise" / "omissions" / "omissions.db"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    OmissionStore(_repo_db(tmp_path)).close()
    (tmp_path / "a.py").write_text("x = 1\n" * 40, encoding="utf-8")
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path), raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "host-session")
    return tmp_path


def _files(repo: Path) -> list[tuple]:
    with OmissionStore(_repo_db(repo)) as store:
        return store._conn.execute(
            "SELECT path, read_tokens, session_key FROM savings_event_files"
        ).fetchall()


def _tool(repo: Path):
    async def get_context(targets: list[str]) -> dict:
        tokens = file_read_tokens(repo, targets[0])
        result = {"targets": {targets[0]: {"type": "file"}}, "_meta": {}}
        declare_replaced(result, tokens or 0)
        return result

    return get_context


@pytest.mark.asyncio
async def test_a_file_card_credits_its_file_keyed_by_the_host_session(repo: Path) -> None:
    from repowise.core.savings.correlation import file_session_key

    out = await tool_middleware(_tool(repo))(["a.py"])
    assert out["targets"] == {"a.py": {"type": "file"}}
    (row,) = _files(repo)
    assert row[0] == "a.py"
    assert row[1] == file_read_tokens(repo, "a.py")
    assert row[2] == file_session_key("host-session")

    with OmissionStore(_repo_db(repo)) as store:
        report = store.savings().report(str(repo), as_of=datetime.now(UTC))
    assert (report.files_not_opened, report.tool_calls_answered) == (1, 1)


@pytest.mark.asyncio
async def test_an_error_reply_credits_no_file(repo: Path) -> None:
    async def get_context(targets: list[str]) -> dict:
        file_read_tokens(repo, targets[0])
        return {"error": "nope"}

    await tool_middleware(get_context)(["a.py"])
    assert _files(repo) == []


@pytest.mark.asyncio
async def test_failing_file_bookkeeping_never_changes_the_reply(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = await tool_middleware(_tool(repo))(["a.py"])

    from repowise.server.mcp_server._savings import event as savings_event
    from repowise.server.mcp_server._savings import interaction

    def boom(*_args, **_kwargs):
        raise RuntimeError("forced")

    monkeypatch.setattr(interaction.Interaction, "note_avoided_file", boom)
    # A non-string key makes the environment lookup raise inside the write.
    monkeypatch.setattr(savings_event, "_HOST_SESSION_ENV", object())
    forced = await tool_middleware(_tool(repo))(["a.py"])
    assert forced == control
    with OmissionStore(_repo_db(repo)) as store:
        # The event still lands; only its file list is lost.
        assert store._conn.execute("SELECT COUNT(*) FROM savings_events").fetchone()[0] == 2


def test_an_ambiguous_symbol_credits_only_files_with_a_rendered_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from repowise.server.mcp_server._savings import reads
    from repowise.server.mcp_server.tool_symbol.ambiguous import _finish_ambiguous

    noted: list[str] = []
    monkeypatch.setattr(reads, "note_served", lambda path, _tokens: noted.append(path))
    response = {
        "candidates": [{"file": "a.py", "source": "1 x = 1"}],
        "note": "",
        "_meta": {},
    }
    over_budget = [{"file": "b.py", "fetch_with": "ref"}]
    _finish_ambiguous(response, 1, over_budget, {"a.py": "x = 1\n", "b.py": "y = 2\n"})
    assert noted == ["a.py"]
