"""The server ``instructions`` string decides whether an agent reaches for the tools.

Hosts that defer tool schemas show only tool names plus this string, and some
truncate long instructions, so the guidance that matters is pinned here.
"""

from __future__ import annotations

from repowise.server.mcp_server import mcp

# Well under the 2,000 chars some hosts keep.
_MAX_INSTRUCTIONS_CHARS = 1500


def test_instructions_fit_the_length_cap() -> None:
    assert len(mcp.instructions) < _MAX_INSTRUCTIONS_CHARS


def test_instructions_say_when_each_tool_replaces_a_text_search() -> None:
    text = mcp.instructions
    assert "search_codebase" in text and "`lines`" in text
    assert 'include=["references"]' in text
    assert "get_answer" in text
    assert "get_risk" in text and "get_change_risk" in text and "get_why" in text
    assert "repowise update --working-tree" in text


def test_instructions_keep_the_no_index_and_workspace_guidance() -> None:
    text = mcp.instructions
    assert "repowise init --yes" in text
    assert "do not run it yourself" in text
    assert "get_architecture" in text and "get_blast_radius" in text
