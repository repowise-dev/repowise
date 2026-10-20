"""``get_answer``'s ``no-llm-provider`` degraded path names what a key or a
local model would add, once per process, never on every call (#3105).
"""

from __future__ import annotations

import pytest

from repowise.server.mcp_server import _meta, _state
from repowise.server.mcp_server.tool_answer.answer import _degraded_payload

HITS = [{"target_path": "pkg/mod.py", "title": "mod", "summary": "s", "score": 1.0}]


@pytest.fixture(autouse=True)
def _clean_keyless_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_state, "_keyless_note_announced", False)


async def _payload(reason: str) -> dict:
    return await _degraded_payload(
        reason=reason,
        note="DEGRADED",
        question="how does mod work",
        hits=HITS,
        fallback_targets=["pkg/mod.py"],
        repository=None,
        t0=0.0,
    )


def test_first_no_llm_provider_response_carries_the_note() -> None:
    first = _meta.keyless_note_meta()
    # Synthesis comes from a provider the server can build, not from generate.
    assert "repowise generate" not in first["keyless_note"]
    assert "restart the MCP server" in first["keyless_note"]


def test_second_response_in_the_same_process_says_nothing() -> None:
    _meta.keyless_note_meta()
    assert _meta.keyless_note_meta() == {}


async def test_degraded_payload_carries_the_note_for_no_llm_provider() -> None:
    payload = await _payload("no-llm-provider")
    assert "provider" in payload["_meta"]["keyless_note"]
    assert _state._keyless_note_announced is True


async def test_degraded_payload_announces_only_once_per_session() -> None:
    first = await _payload("no-llm-provider")
    second = await _payload("no-llm-provider")
    assert "keyless_note" in first["_meta"]
    assert "keyless_note" not in second["_meta"]


async def test_other_degraded_reasons_never_carry_the_note() -> None:
    payload = await _payload("synthesis-failed")
    assert "keyless_note" not in payload["_meta"]


async def test_another_reason_does_not_use_up_the_one_announcement() -> None:
    await _payload("synthesis-failed")
    assert _state._keyless_note_announced is False
    later = await _payload("no-llm-provider")
    assert "keyless_note" in later["_meta"]
