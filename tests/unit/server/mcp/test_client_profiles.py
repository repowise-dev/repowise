"""Each MCP client is listed its own default profile when nothing is configured.

A host that defers tool schemas (Claude Code) keeps the standard surface; any
other client, which pays every listed schema on every turn, gets ``core``. An
explicit ``--tools`` or ``mcp.tools`` choice applies to every client.
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp.types import Implementation

from repowise.core.registry.tool_selection import CORE_TOOLS

_STANDARD = {
    "get_answer",
    "get_context",
    "get_symbol",
    "search_codebase",
    "get_risk",
    "get_change_risk",
    "get_why",
    "get_overview",
    "get_health",
    "get_dead_code",
}


async def _listed(tmp_path: Any, monkeypatch: Any, client: str | None, tools: Any) -> tuple[set[str], str]:
    from mcp.shared.memory import create_connected_server_and_client_session

    from repowise.server.mcp_server import _state, _tool_selection, create_mcp_server, mcp

    monkeypatch.setattr(_tool_selection, "_is_workspace", lambda _path: False)
    monkeypatch.setattr(_state, "_repo_path", _state._repo_path)
    monkeypatch.setattr(_state, "_force_single_repo", _state._force_single_repo)
    monkeypatch.setattr(mcp._mcp_server, "instructions", mcp.instructions)
    info = Implementation(name=client, version="1") if client else None
    try:
        server = create_mcp_server(str(tmp_path), tools=tools)
        async with create_connected_server_and_client_session(
            server._mcp_server, client_info=info
        ) as session:
            listed = {tool.name for tool in (await session.list_tools()).tools}
            instructions = server.instructions
            # Listing narrows, it does not unregister: an unlisted tool still runs.
            assert {t.name for t in server._tool_manager.list_tools()} >= listed
        return listed, instructions
    finally:
        if _tool_selection._full_surface is not None:
            mcp._tool_manager._tools = dict(_tool_selection._full_surface)
        _tool_selection._selected_surface = None
        _tool_selection._boot_inputs = None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client", "expected"),
    [
        ("claude-code", _STANDARD),
        ("cursor-vscode", set(CORE_TOOLS)),
        ("codex-mcp-client", set(CORE_TOOLS)),
        ("Visual Studio Code", set(CORE_TOOLS)),
        ("some-new-host", set(CORE_TOOLS)),
    ],
)
async def test_default_is_per_client(tmp_path, monkeypatch, client, expected) -> None:
    listed, instructions = await _listed(tmp_path, monkeypatch, client, None)
    assert listed == expected
    # Some client is not listed get_risk, so the instructions say how to add it.
    assert "mcp.tools standard" in instructions


@pytest.mark.asyncio
@pytest.mark.parametrize("client", ["claude-code", "cursor-vscode"])
async def test_an_explicit_profile_applies_to_every_client(tmp_path, monkeypatch, client) -> None:
    listed, instructions = await _listed(tmp_path, monkeypatch, client, "standard")
    assert listed == _STANDARD
    assert "mcp.tools standard" not in instructions

    listed, _ = await _listed(tmp_path, monkeypatch, client, "core +get_health")
    assert listed == set(CORE_TOOLS) | {"get_health"}


@pytest.mark.asyncio
async def test_deltas_adjust_each_clients_default(tmp_path, monkeypatch) -> None:
    cursor, _ = await _listed(tmp_path, monkeypatch, "cursor-vscode", "+get_health,-get_why")
    assert cursor == (set(CORE_TOOLS) | {"get_health"}) - {"get_why"}
    claude, _ = await _listed(tmp_path, monkeypatch, "claude-code", "+get_health,-get_why")
    assert claude == _STANDARD - {"get_why"}
