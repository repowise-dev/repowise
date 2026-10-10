"""``get_answer``'s ``no-llm-provider`` degraded path names what a key or a
local model would add, once per process, never on every call (#3105).
"""

from __future__ import annotations

import pytest

from repowise.server.mcp_server import _meta, _state


@pytest.fixture(autouse=True)
def _clean_keyless_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_state, "_keyless_note_announced", False)


def test_first_no_llm_provider_response_carries_the_note() -> None:
    first = _meta.keyless_note_meta()
    assert "repowise generate" in first["keyless_note"]


def test_second_response_in_the_same_process_says_nothing() -> None:
    _meta.keyless_note_meta()
    assert _meta.keyless_note_meta() == {}


def test_a_response_with_a_provider_never_carries_it() -> None:
    # keyless_note_meta is only ever called from the no-llm-provider path
    # (tool_answer/degraded.py); a response with a provider configured
    # never reaches it at all, so there is nothing to announce.
    assert _state._keyless_note_announced is False
