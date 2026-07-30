from __future__ import annotations

import json
from pathlib import Path

from repowise.core.sessions import CodexAdapter, Event

from repowise.core.sessions.miners.demand import mine_events_demand
from repowise.core.sessions.miners.decisions import mine_events

FIXTURE = Path(__file__).parent / "data" / "codex_session.jsonl"
REPO_PREFIX = "c:\\users\\x\\repo"

ADAPTER = CodexAdapter()


def test_discover_lists_jsonl_sorted(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    (root / "2026" / "07" / "13").mkdir(parents=True)
    (root / "2026" / "07" / "13" / "b.jsonl").write_text("", encoding="utf-8")
    (root / "2026" / "07" / "13" / "a.jsonl").write_text("", encoding="utf-8")
    (root / "2026" / "07" / "13" / "notes.txt").write_text("", encoding="utf-8")

    found = ADAPTER.discover(Path("/tmp/repo"), projects_root=root)

    assert [p.name for p in found] == ["a.jsonl", "b.jsonl"]


def test_normalize_real_codex_rollout() -> None:
    events = list(ADAPTER.iter_events(FIXTURE))

    meta = next(e for e in events if e.kind == "session_meta")
    assistant = next(e for e in events if e.kind == "assistant")
    tool = next(e for e in events if e.tool_uses) 

    assert isinstance(meta, Event)
    assert meta.kind == "session_meta"
    assert meta.is_meta is True

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
    
def test_iter_events_threads_session_id() -> None:
    events = list(ADAPTER.iter_events(FIXTURE))

    session = events[0].session_id

    assert session is not None
    assert all(e.session_id == session for e in events)

def test_codex_rollout_feeds_demand_miner() -> None:
    events = list(ADAPTER.iter_events(FIXTURE))


    demand = mine_events_demand(events, REPO_PREFIX)

    # assert demand

def test_codex_rollout_feeds_decision_miner() -> None:
    events = list(ADAPTER.iter_events(FIXTURE))


    decisions = mine_events(events, REPO_PREFIX)

    # will need to update these after finishing with the adapter change so miners receive correct inputs and produce an output, also transcript will be updated then these asserts will be aligned
    # assert len(decisions) == 1

    # decision = decisions[0]
    # assert decision.kind == "explicit_choice"
    # assert decision.files == ["pkg/app.py"]
    # assert decision.quotes == [
    #     "We chose Flask because it is lightweight, keeps SQLite integration straightforward, and avoids unnecessary boilerplate for this small application."
    # ]
    