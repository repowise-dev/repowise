"""Chat system prompt must match the live tool registry size."""

from __future__ import annotations

from repowise.server.chat_tools import get_tool_registry
from repowise.server.routers.chat import _build_system_prompt


def test_system_prompt_tool_count_matches_registry():
    n = len(get_tool_registry())
    prompt = _build_system_prompt("demo", "/tmp/demo")
    assert f"{n} specialized tools" in prompt
    assert "get_change_risk" in prompt
