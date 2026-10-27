"""Per-agent capture adapters on real hook payload shapes, and the transcript seam.

Fixtures live in ``tests/fixtures/attribution_capture``; code and ids are
synthetic, shapes are real. Sources: Claude Code from the 2.1.280 binary's
PostToolUse builder plus local transcripts (``MultiEdit`` is the documented
legacy shape, not a tool in that build); Codex from openai/codex
``post-tool-use.command.input.schema.json`` and ``core/tests/suite/hooks.rs``;
Cursor from cursor.com/docs/agent/hooks (``afterFileEdit``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.agents.identity import all_identities
from repowise.core.edit_capture.edits import CAPTURE_ADAPTERS, EXTRACTORS
from repowise.core.edit_capture.hashing import line_hash
from repowise.core.sessions.adapters import get_adapter, registered_adapters
from repowise.core.sessions.events import ToolResult, ToolUse

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "attribution_capture"

BILLING = [
    "    subtotal = sum(item.price for item in items)",
    "    return round(subtotal * 1.2, 2)",
]


def _payload(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _call(adapter: str, name: str):
    return CAPTURE_ADAPTERS[adapter](_payload(name))


def test_claude_code_edit_reads_the_structured_patch() -> None:
    call = _call("claude_code", "claude_code_edit.json")
    assert call is not None and call.tool == "Edit"
    (edit,) = call.edits
    assert edit.path == "/work/demo/src/billing.py"
    assert edit.added == BILLING
    assert call.session_id and call.call_id == "toolu_01EXAMPLE" and call.model is None
    assert call.cwd == "/work/demo"


def test_claude_code_windows_crlf_edit_hashes_like_the_lf_one() -> None:
    call = _call("claude_code", "claude_code_edit_windows_crlf.json")
    assert call is not None
    (edit,) = call.edits
    assert edit.path == "C:\\work\\demo\\src\\billing.py"
    # No structuredPatch: falls back to new_string minus old_string, CR dropped.
    assert edit.added == BILLING
    lf = _call("claude_code", "claude_code_edit.json").edits[0]
    assert [line_hash(x) for x in edit.added] == [line_hash(x) for x in lf.added]


def test_claude_code_write_takes_the_whole_content() -> None:
    (edit,) = _call("claude_code", "claude_code_write.json").edits
    assert edit.path.endswith("report.py") and len(edit.added) == 4


def test_claude_code_multiedit_takes_each_replacement() -> None:
    (edit,) = _call("claude_code", "claude_code_multiedit.json").edits
    assert edit.added == [
        "TAX_RATE = Decimal('0.20')",
        "    return sum(item.price for item in items) * (1 + TAX_RATE)",
    ]


def test_a_user_modified_edit_is_not_captured() -> None:
    assert _call("claude_code", "claude_code_edit_user_modified.json") is None


def test_codex_apply_patch_splits_files_and_skips_deletes() -> None:
    call = _call("codex", "codex_apply_patch.json")
    assert call is not None and call.model == "gpt-5.5-codex"
    assert {e.path: e.added for e in call.edits} == {
        "src/billing.py": BILLING,
        "src/report.py": ["def render(rows):", "    header = 'name,amount'"],
    }


def test_codex_apply_patch_move_lands_on_the_new_path_with_crlf_stripped() -> None:
    (edit,) = _call("codex", "codex_apply_patch_move.json").edits
    assert edit.path == "src/new_name.py"
    assert edit.added == ["VALUE = compute_default_value()", "LIMIT = compute_default_limit()"]


def test_a_failed_apply_patch_wrote_nothing() -> None:
    assert _call("codex", "codex_apply_patch_failed.json") is None


def test_cursor_after_file_edit() -> None:
    call = _call("cursor", "cursor_after_file_edit.json")
    assert call is not None
    (edit,) = call.edits
    assert edit.path == "/work/demo/src/billing.py" and edit.added == BILLING
    assert call.session_id.startswith("c0ffee00") and call.model == "claude-sonnet-4-5"
    assert call.cwd == "/work/demo" and call.call_id.startswith("gen-42:")


@pytest.mark.parametrize(
    ("adapter", "fixture"),
    [
        ("claude_code", "codex_apply_patch.json"),
        ("codex", "cursor_after_file_edit.json"),
        ("cursor", "claude_code_edit.json"),
    ],
)
def test_an_adapter_ignores_another_agents_payload(adapter: str, fixture: str) -> None:
    assert _call(adapter, fixture) is None


def test_every_declared_capture_adapter_exists_and_matches_its_transcript_adapter() -> None:
    for identity in all_identities():
        if identity.capture_adapter is None:
            continue
        assert identity.capture_adapter in CAPTURE_ADAPTERS
        assert identity.capture_adapter in EXTRACTORS
        if identity.session_adapter:
            assert identity.session_adapter == identity.capture_adapter


def test_claude_transcript_tool_call_goes_through_the_same_extractor() -> None:
    """One parser: a transcript's tool_use and toolUseResult are the hook's input and response."""
    payload = _payload("claude_code_edit.json")
    adapter = get_adapter("claude_code")
    use = ToolUse(id="toolu_01EXAMPLE", name="Edit", input=payload["tool_input"])
    result = ToolResult(tool_use_id=use.id, payload=payload["tool_response"])
    from_transcript = adapter.file_edits(use, result)
    from_hook = _call("claude_code", "claude_code_edit.json").edits
    assert [(e.path, e.added) for e in from_transcript] == [(e.path, e.added) for e in from_hook]
    assert adapter.file_edits(ToolUse(id="x", name="Read", input={"file_path": "a"})) == []


def test_codex_rollout_apply_patch_goes_through_the_same_extractor() -> None:
    patch = _payload("codex_apply_patch.json")["tool_input"]["command"]
    line = json.dumps(
        {
            "timestamp": "2026-10-14T10:00:00Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "call_id": "call_apply_1",
                "name": "apply_patch",
                "input": patch,
            },
        }
    )
    adapter = get_adapter("codex")
    event = adapter.normalize(line)
    assert event is not None
    (use,) = event.tool_uses
    edits = adapter.file_edits(use)
    assert {e.path for e in edits} == {"src/billing.py", "src/report.py"}
    assert "codex" in registered_adapters()


def test_write_overwrite_without_patch_is_a_miss() -> None:
    from repowise.core.edit_capture.edits import claude_code_edits

    same = {"file_path": "a.py", "content": "keep this line\nand this one\n"}
    assert claude_code_edits("Write", same, {"type": "update", "structuredPatch": []}) == []
    assert claude_code_edits("Write", same, None) == []


def test_form_feed_stays_inside_one_line() -> None:
    from repowise.core.edit_capture.hashing import split_lines

    assert split_lines("a\x0cb\r\nc\n") == ["a\x0cb", "c"]
