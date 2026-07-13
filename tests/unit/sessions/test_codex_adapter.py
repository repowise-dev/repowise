from __future__ import annotations

import json
from pathlib import Path

from repowise.core.sessions import CodexAdapter, Event

FIXTURE = Path(__file__).parent / "data" / "codex_session.jsonl"

ADAPTER = CodexAdapter()


def test_discover_lists_jsonl_sorted(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    (root / "2026" / "07" / "13").mkdir(parents=True)
    (root / "2026" / "07" / "13" / "b.jsonl").write_text("", encoding="utf-8")
    (root / "2026" / "07" / "13" / "a.jsonl").write_text("", encoding="utf-8")
    (root / "2026" / "07" / "13" / "notes.txt").write_text("", encoding="utf-8")

    found = ADAPTER.discover(Path("/tmp/repo"), projects_root=root)

    assert [p.name for p in found] == ["a.jsonl", "b.jsonl"]


def test_normalize_maps_common_fields_and_tools() -> None:
    event = ADAPTER.normalize((FIXTURE.read_text(encoding="utf-8").splitlines()[0]))

    assert isinstance(event, Event)
    assert event.kind == "assistant"
    assert event.session_id == "sess-1"
    assert event.cwd == r"C:\Users\x\repo"
    assert event.model == "gpt-5-codex"
    assert event.message_id == "msg_01"
    assert event.text == "Searching the codebase."
    assert event.tool_uses[0].name == "search_codebase"


def test_normalize_handles_session_meta_and_custom_tool_payloads() -> None:
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

    meta_event = ADAPTER.normalize(session_meta)
    call_event = ADAPTER.normalize(tool_call)
    output_event = ADAPTER.normalize(tool_output)

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
