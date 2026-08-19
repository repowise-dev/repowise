from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.sessions import CodexAdapter, Event
from repowise.core.sessions.adapters.codex import _normalize_tool_name
from repowise.core.sessions.miners.decisions import mine_events

FIXTURE = Path(__file__).parent / "data" / "codex_session.jsonl"
#: The miners lowercase each event's ``cwd`` before comparing, so a prefix with
#: capitals in it matches nothing. Passing the fixture's own cwd verbatim looks
#: right and silently scopes every assertion to zero events.
REPO_PREFIX = r"C:\Users\Rehan\Documents\Codex\2026-07-14\c".lower()


@pytest.fixture
def adapter() -> CodexAdapter:
    """A fresh adapter per test: correlation state is per-file, not global."""
    return CodexAdapter()


def test_discover_lists_jsonl_sorted(adapter: CodexAdapter, tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    (root / "2026" / "07" / "13").mkdir(parents=True)
    (root / "2026" / "07" / "13" / "b.jsonl").write_text("", encoding="utf-8")
    (root / "2026" / "07" / "13" / "a.jsonl").write_text("", encoding="utf-8")
    (root / "2026" / "07" / "13" / "notes.txt").write_text("", encoding="utf-8")

    found = adapter.discover(Path("/tmp/repo"), projects_root=root)

    assert [p.name for p in found] == ["a.jsonl", "b.jsonl"]


def test_normalize_real_codex_rollout(adapter: CodexAdapter) -> None:
    events = list(adapter.iter_events(FIXTURE))

    meta = next(e for e in events if e.kind == "session_meta")

    assert isinstance(meta, Event)
    assert meta.kind == "session_meta"
    assert meta.is_meta is True


def test_normalize_handles_session_meta_and_custom_tool_payloads(adapter: CodexAdapter) -> None:
    session_meta = json.dumps(
        {
            "timestamp": "2026-07-12T14:39:30.546Z",
            "type": "session_meta",
            "payload": {
                "session_id": "sess-2",
                "id": "sess-2",
                "timestamp": "2026-07-12T14:39:30.546Z",
                "cwd": r"C:\Users\x\repo",
            },
        }
    )
    tool_call = json.dumps(
        {
            "timestamp": "2026-07-12T14:39:31.000Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "id": "ctc_1",
                "call_id": "call_1",
                "name": "exec",
                "input": {"command": "pwd"},
            },
        }
    )
    tool_output = json.dumps(
        {
            "timestamp": "2026-07-12T14:39:31.100Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "call_1",
                "output": [{"type": "input_text", "text": "done"}],
            },
        }
    )

    meta_event = adapter.normalize(session_meta)
    call_event = adapter.normalize(tool_call)
    output_event = adapter.normalize(tool_output)

    assert meta_event is not None
    assert meta_event.kind == "session_meta"
    assert meta_event.session_id == "sess-2"
    assert meta_event.cwd == r"C:\Users\x\repo"
    assert meta_event.is_meta is True

    assert call_event is not None
    assert call_event.tool_uses[0].name == "bash"
    assert call_event.tool_uses[0].id == "call_1"

    assert output_event is not None
    assert output_event.tool_results[0].tool_use_id == "call_1"
    assert output_event.text == "done"


def test_iter_events_threads_session_id(adapter: CodexAdapter) -> None:
    events = list(adapter.iter_events(FIXTURE))

    session = events[0].session_id

    assert session is not None
    assert all(e.session_id == session for e in events)


def test_iter_events_threads_cwd(adapter: CodexAdapter) -> None:
    """Codex writes cwd once. Without threading it, repo scoping is a no-op."""
    events = list(adapter.iter_events(FIXTURE))

    assert all(e.cwd for e in events)


def test_another_repos_session_is_scoped_out(adapter: CodexAdapter) -> None:
    """The guard the threaded cwd exists for: one repo's sessions stay its own."""
    events = list(adapter.iter_events(FIXTURE))

    assert mine_events(events, r"d:\some\other\repo") == []


def test_codex_rollout_feeds_decision_miner(adapter: CodexAdapter) -> None:
    events = list(adapter.iter_events(FIXTURE))

    decisions = mine_events(events, REPO_PREFIX)

    assert len(decisions) == 1

    decision = decisions[0]

    assert decision.kind == "explicit_choice"
    assert decision.files == ["outputs/sqlite-signup-demo/app.py"]
    assert decision.session_id is not None

    assert decision.quotes == [
        "We chose Flask because it is lightweight, keeps SQLite integration straightforward, and avoids unnecessary boilerplate for this small application."
    ]


def test_normalize_exec_rg_to_search_codebase():
    assert (
        _normalize_tool_name(
            "exec",
            {
                "command": ('const r = await tools.shell_command({"command":"rg --files"});'),
            },
        )
        == "search_codebase"
    )


def test_normalize_handles_mcp_function_call_and_output(adapter: CodexAdapter) -> None:
    tool_call = json.dumps(
        {
            "timestamp": "2026-07-17T16:46:00.000Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "search_codebase",
                "namespace": "mcp__repowise",
                "arguments": json.dumps({"query": "signup"}),
                "call_id": "call_mcp_1",
            },
        }
    )

    tool_output = json.dumps(
        {
            "timestamp": "2026-07-17T16:46:01.000Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call_mcp_1",
                "output": (
                    'Wall time: 0.1 seconds\n'
                    'Output:\n'
                    '{"result":{"results":[{"file":"app.py"}]}}'
                ),
            },
        }
    )

    call_event = adapter.normalize(tool_call)
    output_event = adapter.normalize(tool_output)

    assert call_event is not None
    assert call_event.tool_uses[0].id == "call_mcp_1"
    assert call_event.tool_uses[0].name == "search_codebase"
    assert call_event.tool_uses[0].input["query"] == "signup"
    assert call_event.tool_uses[0].input["path"] == "app.py"
    assert output_event is not None
    assert output_event.tool_results[0].tool_use_id == "call_mcp_1"

    # The output should bind the returned file to the original tool call.
    assert call_event.tool_uses[0].input["path"] == "app.py"


def _search_then_output(output: object) -> str:
    """An rg search whose result is *output*, as one raw transcript line."""
    return json.dumps(
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "call_rg",
                "output": output,
            },
        }
    )


RG_CALL = json.dumps(
    {
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "call_id": "call_rg",
            "name": "exec",
            "input": 'const r = await tools.shell_command({"command":"rg TODO src"});',
        },
    }
)


def test_a_content_match_binds_the_path_not_the_matched_line(adapter: CodexAdapter) -> None:
    """``rg`` prints ``path:line:text``. Only the path half is a file."""
    adapter.normalize(RG_CALL)
    event = adapter.normalize(
        _search_then_output(
            [{"type": "input_text", "text": "Exit code: 0\nOutput:\nsrc/app.py:12:    # TODO\n"}]
        )
    )

    assert event is not None
    results = json.loads(event.tool_results[0].content)["result"]["results"]
    assert results == [{"file": "src/app.py"}]


def test_unreadable_search_output_neither_raises_nor_erases(adapter: CodexAdapter) -> None:
    """``normalize`` may not raise on content, and must not drop a real result."""
    adapter.normalize(RG_CALL)
    event = adapter.normalize(_search_then_output(None))

    assert event is not None
    assert event.tool_results[0].content is None


def test_a_string_search_output_survives_rewriting(adapter: CodexAdapter) -> None:
    adapter.normalize(RG_CALL)
    event = adapter.normalize(_search_then_output("Output:\nsrc/app.py\n"))

    assert event is not None
    assert event.tool_results[0].content == "Output:\nsrc/app.py\n"
