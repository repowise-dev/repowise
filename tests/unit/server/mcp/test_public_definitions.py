"""Another server can send what this one sends without starting it.

``server_instructions`` and ``tool_definitions`` are the public form of the
instructions and the ``tools/list`` entries; each profile's live server output
must match them byte for byte.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import pytest

from repowise.core.registry.instructions import server_instructions
from repowise.server.mcp_server import mcp, tool_definitions


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["core", "lean", "standard", "all"])
@pytest.mark.parametrize("workspace", [False, True])
async def test_public_definitions_match_the_live_server_byte_for_byte(
    tmp_path: Any, monkeypatch: Any, profile: str, workspace: bool
) -> None:
    from repowise.server.mcp_server import _state, _tool_selection, create_mcp_server

    monkeypatch.setattr(_tool_selection, "_is_workspace", lambda _path: workspace)
    monkeypatch.setattr(_state, "_repo_path", _state._repo_path)
    monkeypatch.setattr(_state, "_force_single_repo", _state._force_single_repo)
    monkeypatch.setattr(mcp._mcp_server, "instructions", mcp.instructions)
    try:
        server = create_mcp_server(str(tmp_path), tools=profile)
        listed = [
            t.model_dump(by_alias=True, mode="json", exclude_none=True)
            for t in await server.list_tools()
        ]
        served = [tool["name"] for tool in listed]
        assert json.dumps(tool_definitions(served)) == json.dumps(listed)
        assert server.instructions == server_instructions(served, workspace=workspace)
    finally:
        if _tool_selection._full_surface is not None:
            mcp._tool_manager._tools = dict(_tool_selection._full_surface)
        _tool_selection._selected_surface = None


def test_tool_definitions_create_no_server() -> None:
    code = (
        "import sys\n"
        "from repowise.server.mcp_server import tool_definitions\n"
        "assert [t['name'] for t in tool_definitions(['get_why', 'get_answer', 'nope'])]"
        " == ['get_answer', 'get_why']\n"
        "assert 'repowise.server.mcp_server._server' not in sys.modules\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_workspace_tools_are_named_only_in_a_workspace() -> None:
    served = {"get_answer", "get_architecture"}
    assert "get_architecture" not in server_instructions(served)
    assert "get_architecture" in server_instructions(served, workspace=True)


def test_extra_lines_close_the_instructions() -> None:
    text = server_instructions({"get_answer"}, extra=["", "No checkout here."])
    assert text == server_instructions({"get_answer"}) + "\n\nNo checkout here."
