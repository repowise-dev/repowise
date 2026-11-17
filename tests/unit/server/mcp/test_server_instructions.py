"""The server ``instructions`` string decides whether an agent reaches for the tools.

Hosts that defer tool schemas show only tool names plus this string, and some
truncate long instructions, so the guidance that matters is pinned here.
"""

from __future__ import annotations

from typing import Any

import pytest

from repowise.server.mcp_server import mcp

# Well under the 2,000 chars some hosts keep.
_MAX_INSTRUCTIONS_CHARS = 1500
#: The base text before a repository or workspace line is added.
_BASE_INSTRUCTIONS_CHARS = 720


def test_instructions_fit_the_length_cap() -> None:
    assert len(mcp.instructions) < _MAX_INSTRUCTIONS_CHARS


def test_instructions_say_when_each_tool_replaces_a_text_search() -> None:
    text = mcp.instructions
    assert "search_codebase" in text and "`lines`" in text
    assert 'include=["references"]' in text
    assert "get_answer" in text and "per call" in text
    assert 'include=["files"]' in text
    assert "get_risk" in text and "get_change_risk" in text and "get_why" in text
    assert "repowise update" not in text


def test_instructions_stay_short() -> None:
    # Read into every session and paid again per registration; a response
    # already flags what it says about uncommitted edits, so that stays there.
    # Built here, not read from ``mcp.instructions``, which any earlier
    # create_mcp_server call in the process rewrites.
    from repowise.server.mcp_server._server import _instructions

    base = _instructions()
    assert len(base) <= _BASE_INSTRUCTIONS_CHARS
    assert "uncommitted" not in base


def test_instructions_keep_the_no_index_and_workspace_guidance() -> None:
    text = mcp.instructions
    assert "repowise init --yes" in text
    assert "do not run it yourself" in text


@pytest.mark.parametrize(("override", "named"), [(None, False), ("all", True)])
def test_workspace_tools_are_named_only_when_served(
    tmp_path: Any, monkeypatch: Any, override: str | None, named: bool
) -> None:
    # Both are opt-in, so a default workspace server must not advertise them.
    from repowise.server.mcp_server import _state, _tool_selection, create_mcp_server

    monkeypatch.setattr(_tool_selection, "_is_workspace", lambda _path: True)
    monkeypatch.setattr(_state, "_repo_path", _state._repo_path)
    monkeypatch.setattr(_state, "_force_single_repo", _state._force_single_repo)
    monkeypatch.setattr(mcp._mcp_server, "instructions", mcp.instructions)
    try:
        server = create_mcp_server(str(tmp_path), tools=override)
        served = {t.name for t in server._tool_manager.list_tools()}
        for tool in ("get_architecture", "get_blast_radius"):
            assert (tool in served) is named
            assert (tool in server.instructions) is named
        assert len(server.instructions) < _MAX_INSTRUCTIONS_CHARS
    finally:
        if _tool_selection._full_surface is not None:
            mcp._tool_manager._tools = dict(_tool_selection._full_surface)
        _tool_selection._selected_surface = None


def test_handshake_reports_the_repowise_version_not_the_sdk_version() -> None:
    from repowise.server import __version__

    options = mcp._mcp_server.create_initialization_options()
    assert options.server_name == "repowise"
    assert options.server_version == __version__


@pytest.mark.parametrize(
    "override",
    [None, "all", "core", "lean", "-get_answer", "-get_why", "get_context,search_codebase"],
)
def test_instructions_name_no_tool_the_server_does_not_serve(
    tmp_path: Any, monkeypatch: Any, override: str | None
) -> None:
    # A host calls a tool it reads in the instructions even when it is not
    # listed, and the call fails.
    import re

    from repowise.core.registry import mcp_tool_registry
    from repowise.server.mcp_server import _state, _tool_selection, create_mcp_server

    monkeypatch.setattr(_state, "_repo_path", _state._repo_path)
    monkeypatch.setattr(_state, "_force_single_repo", _state._force_single_repo)
    monkeypatch.setattr(mcp._mcp_server, "instructions", mcp.instructions)
    try:
        server = create_mcp_server(str(tmp_path), tools=override)
        served = {t.name for t in server._tool_manager.list_tools()}
        named = {
            entry.name
            for entry in mcp_tool_registry.entries()
            if re.search(rf"\b{entry.name}\b", server.instructions)
        }
        assert named and named <= served
    finally:
        if _tool_selection._full_surface is not None:
            mcp._tool_manager._tools = dict(_tool_selection._full_surface)
        _tool_selection._selected_surface = None


def test_the_decisions_line_is_dropped_without_get_why() -> None:
    from repowise.server.mcp_server._server import _instructions

    line = "- 3 decisions govern files here: get_why(targets=[file]) first."
    assert line in _instructions({"get_why"}, line)
    assert "get_why" not in _instructions({"get_answer", "get_risk"}, line)
