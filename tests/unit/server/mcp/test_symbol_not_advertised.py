"""``get_symbol`` is served, and is not advertised as somewhere to start.

Where a short capability list names ``get_symbol`` beside ``get_answer``, an
agent spends the large majority of its retrieval calls fetching one symbol at
a time, and ends up making more tool calls than an agent with no tools at all
— a per-symbol tool supplements navigation rather than replacing it. Given the
same surface with no such list, agents reach for ``get_answer`` instead.
``get_symbol`` is also the one tool whose payload cannot be trimmed, so its
calls are the ones no size work can reach.

So the lever is what the surfaces SAY, not what they serve, and these tests
pin the saying.

**Not a tool-count test, deliberately.** Harnesses that defer tool schemas
read the CLAUDE.md table and nothing else until they search, so serving fewer
tools changes nothing an agent sees. ``get_symbol`` must stay registered and
stay in the lean profile; the first test here is what stops a future reader of
this file from "fixing" the problem by removing the tool.
"""

from __future__ import annotations

from repowise.cli.commands.augment_cmd.session_start import _CORE_TOOLS
from repowise.core.generation.editor_files.tool_table import TOOL_TABLE_ROWS
from repowise.server.mcp_server._tool_selection import LEAN_TOOLS


def test_get_symbol_is_still_served():
    assert "get_symbol" in LEAN_TOOLS
    assert "get_symbol" in TOOL_TABLE_ROWS


def test_session_start_block_names_entry_points_only():
    """The one block every session reads first, before any tool description."""
    assert "get_symbol" not in _CORE_TOOLS
    for entry_point in ("get_answer", "get_context", "search_codebase"):
        assert entry_point in _CORE_TOOLS


def test_the_tool_table_row_leads_with_what_get_symbol_is_not():
    """A row is an advertisement. This one has to read like a follow-up."""
    _signature, row = TOOL_TABLE_ROWS["get_symbol"]
    assert "not an entry point" in row.lower()
    # It still has to tell the agent how to call it successfully.
    assert "::" in row


def test_the_coarse_rows_say_the_body_already_arrived():
    """Half the lever: the alternative has to be visible in the same table.

    Removing ``get_symbol``'s advertising without saying where bodies DO come
    from does not save anything; it just moves the agent to a Grep.
    """
    _sig, answer_row = TOOL_TABLE_ROWS["get_answer"]
    assert "symbol_bodies" in answer_row
    _sig, context_row = TOOL_TABLE_ROWS["get_context"]
    assert "skeleton" in context_row


def test_get_symbol_schema_description_is_a_follow_up():
    """What Codex reads up front, and Claude Code reads after a ToolSearch."""
    from repowise.server.mcp_server.tool_symbol import get_symbol

    doc = (get_symbol.__doc__ or "").lower()
    assert "not an entry point" in doc
    assert "symbol by symbol" in doc


def test_get_context_no_longer_points_per_symbol_at_get_symbol():
    """It used to say "symbol_ids to pipe into get_symbol (cheaper than Read)".

    That is the per-signature walk, recommended by the entry point itself, in
    a description shipped on every call.
    """
    from repowise.server.mcp_server.tool_context import get_context

    doc = get_context.__doc__ or ""
    assert "pipe into get_symbol" not in doc
    assert 'include=["skeleton"]' in doc
