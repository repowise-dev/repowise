"""Claude Code adapter — discovery, normalization, and the schema gotchas.

The checked-in fixture (``data/claude_code_session.jsonl``) mirrors real
transcript shapes captured on this machine: split usage blocks sharing one
``message.id``, sidechain and meta/compact-summary lines, dict and
error-string ``toolUseResult`` payloads, an interrupt marker, and malformed
lines.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.sessions import (
    ClaudeCodeAdapter,
    Event,
    iter_deduped_usage,
    parse_timestamp,
    transcript_dir_for,
)

FIXTURE = Path(__file__).parent / "data" / "claude_code_session.jsonl"

ADAPTER = ClaudeCodeAdapter()


@pytest.fixture()
def events() -> list[Event]:
    return list(ADAPTER.iter_events(FIXTURE))


def test_transcript_dir_munging() -> None:
    assert (
        transcript_dir_for(Path(r"C:\Users\x\Desktop\repo"), Path("/p")).name
        == "C--Users-x-Desktop-repo"
    )


def test_discover_lists_jsonl_sorted(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    transcripts = transcript_dir_for(repo, tmp_path / "projects")
    transcripts.mkdir(parents=True)
    (transcripts / "b.jsonl").write_text("", encoding="utf-8")
    (transcripts / "a.jsonl").write_text("", encoding="utf-8")
    (transcripts / "notes.txt").write_text("", encoding="utf-8")
    found = ADAPTER.discover(repo, projects_root=tmp_path / "projects")
    assert [p.name for p in found] == ["a.jsonl", "b.jsonl"]


def test_discover_absent_dir_is_empty(tmp_path: Path) -> None:
    assert ADAPTER.discover(tmp_path / "repo", projects_root=tmp_path / "nowhere") == []


def test_malformed_lines_normalize_to_none(events: list[Event]) -> None:
    # 13 fixture lines: 11 valid entries, one non-JSON line, one non-dict line.
    assert len(events) == 11
    assert ADAPTER.normalize("not json at all") is None
    assert ADAPTER.normalize("[1, 2, 3]") is None
    assert ADAPTER.normalize('{"no_type_field": true}') is None


def test_common_fields(events: list[Event]) -> None:
    first = events[0]
    assert first.kind == "assistant"
    assert first.session_id == "sess-1"
    assert first.cwd == r"C:\Users\x\repo"
    assert first.model == "claude-fable-5"
    assert first.message_id == "msg_01"
    assert first.text == "Running the tests now."
    assert first.ts == parse_timestamp("2026-07-11T10:00:00.000Z")


def test_tool_use_blocks(events: list[Event]) -> None:
    use = events[1].tool_uses[0]
    assert (use.id, use.name) == ("toolu_01", "Bash")
    assert use.input == {"command": "pytest -q"}


def test_tool_result_success_and_error_payloads(events: list[Event]) -> None:
    ok = events[2].tool_results[0]
    assert ok.tool_use_id == "toolu_01"
    assert ok.is_error is False
    assert ok.content == "3 passed"
    assert ok.payload == {"stdout": "3 passed", "stderr": ""}

    failed = events[4].tool_results[0]
    assert failed.tool_use_id == "toolu_02"
    assert failed.is_error is True
    assert isinstance(failed.payload, str)
    assert failed.payload.startswith("Error: Exit code 4")


def test_sidechain_events_are_kept_and_flagged(events: list[Event]) -> None:
    sidechain = events[5]
    assert sidechain.sidechain is True
    assert sidechain.text == "Subagent reporting back."
    assert all(not e.sidechain for e in events if e is not sidechain)


def test_meta_and_compact_summary_flags(events: list[Event]) -> None:
    meta = events[6]
    assert meta.is_meta is True
    assert "system-reminder" in meta.text  # string content passes through
    compact = events[7]
    assert compact.is_compact_summary is True


def test_interrupt_marker(events: list[Event]) -> None:
    interrupted = events[8]
    assert interrupted.interrupted is True
    assert "use approach B instead" in interrupted.text
    assert not events[0].interrupted


def test_other_entry_types_pass_kind_through(events: list[Event]) -> None:
    assert events[9].kind == "system"
    assert events[10].kind == "file-history-snapshot"


def test_usage_dedup_by_message_id(events: list[Event]) -> None:
    # msg_01 spans two lines (text block, then tool_use block) with the same
    # usage object; counting both would double-bill the message.
    counted = list(iter_deduped_usage(events))
    assert [e.message_id for e in counted] == ["msg_01", "msg_02", "msg_03"]
    assert sum(e.usage["input_tokens"] for e in counted) == 300


def test_prefilter_skips_lines_without_parsing(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    lines = [
        json.dumps({"type": "assistant", "message": {"content": "plain text"}}),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]
                },
            }
        ),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    got = list(ADAPTER.iter_events(path, prefilter=lambda raw: '"tool_use"' in raw))
    assert len(got) == 1
    assert got[0].tool_uses[0].id == "t1"
