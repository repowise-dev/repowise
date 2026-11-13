"""Each MCP client is listed its own default profile when nothing is configured.

A host that defers tool schemas (Claude Code) keeps the standard surface; any
other client, which pays every listed schema on every turn, gets ``core``. An
explicit ``--tools`` or ``mcp.tools`` choice applies to every client.
"""

from __future__ import annotations

import re
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


async def _session(
    tmp_path: Any, monkeypatch: Any, client: str | None, tools: Any, *, workspace: bool = False
) -> tuple[set[str], str, str]:
    """What one client session is listed, the boot text, and the text it is sent."""
    from mcp.shared.memory import create_connected_server_and_client_session

    from repowise.server.mcp_server import _server, _state, _tool_selection, create_mcp_server, mcp

    monkeypatch.setattr(_tool_selection, "_is_workspace", lambda _path: workspace)
    # The session runs the server lifespan, which records process state (the
    # embedder status among it) that later tests' payloads read: put it all back.
    for name, value in list(vars(_state).items()):
        if name.startswith("_") and not name.startswith("__") and not callable(value):
            monkeypatch.setattr(_state, name, value)
    monkeypatch.setattr(mcp._mcp_server, "instructions", mcp.instructions)
    monkeypatch.setattr(_server, "_client_instructions", _server._client_instructions)
    info = Implementation(name=client, version="1") if client else None
    try:
        server = create_mcp_server(str(tmp_path), tools=tools)
        async with create_connected_server_and_client_session(
            server._mcp_server, client_info=info
        ) as session:
            listed = {tool.name for tool in (await session.list_tools()).tools}
            # The connect helper drops the initialize reply; asking again repeats it.
            sent = (await session.initialize()).instructions or ""
            instructions = server.instructions
            # Listing narrows, it does not unregister: an unlisted tool still runs.
            assert {t.name for t in server._tool_manager.list_tools()} >= listed
        return listed, instructions, sent
    finally:
        if _tool_selection._full_surface is not None:
            mcp._tool_manager._tools = dict(_tool_selection._full_surface)
        _tool_selection._selected_surface = None
        _tool_selection._boot_inputs = None


async def _listed(tmp_path: Any, monkeypatch: Any, client: str | None, tools: Any) -> tuple[set[str], str]:
    listed, instructions, _ = await _session(tmp_path, monkeypatch, client, tools)
    return listed, instructions


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


@pytest.mark.asyncio
@pytest.mark.parametrize("tools", ["core", "-get_risk,-get_change_risk", "get_answer,get_why"])
async def test_no_notice_when_the_setting_itself_leaves_them_out(tmp_path, monkeypatch, tools) -> None:
    _, instructions = await _listed(tmp_path, monkeypatch, "cursor-vscode", tools)
    assert "mcp.tools standard" not in instructions



_TOOL_NAME = re.compile(r"\b(?:get_[a-z_]+|search_codebase|list_repos)\b")


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace", [False, True], ids=["single", "workspace"])
@pytest.mark.parametrize(
    "tools", [None, "core", "lean", "standard", "all", "+get_risk", "core +get_health", "-get_why"]
)
@pytest.mark.parametrize("client", ["claude-code", "cursor-vscode", "codex-mcp-client", None])
async def test_a_session_is_told_only_the_tools_it_is_listed(
    tmp_path, monkeypatch, client, tools, workspace
) -> None:
    # A client acts on a tool it reads about even when it was never listed it.
    listed, _, sent = await _session(tmp_path, monkeypatch, client, tools, workspace=workspace)
    named = set(_TOOL_NAME.findall(sent))
    assert named and named <= listed, sent


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace", [False, True], ids=["single", "workspace"])
@pytest.mark.parametrize("tools", [None, "core", "lean", "standard", "all", "+get_health"])
async def test_claude_code_is_sent_the_boot_text(tmp_path, monkeypatch, tools, workspace) -> None:
    _, boot, sent = await _session(tmp_path, monkeypatch, "claude-code", tools, workspace=workspace)
    assert sent == boot


@pytest.mark.asyncio
async def test_a_core_client_is_not_sent_the_standard_checks(tmp_path, monkeypatch) -> None:
    _, boot, sent = await _session(tmp_path, monkeypatch, "cursor-vscode", None)
    assert "get_risk" in boot and "mcp.tools standard" in boot
    assert "get_risk" not in sent and "get_change_risk" not in sent
    assert "mcp.tools standard" not in sent
    assert "get_why before changing an established pattern" in sent
    assert len(sent) < len(boot)


@pytest.mark.asyncio
async def test_a_failing_per_client_text_falls_back_to_the_boot_text(tmp_path, monkeypatch) -> None:
    from repowise.server.mcp_server import _server

    def broken(_select: Any):
        def select(repo_path: Any, tools: Any) -> set[str]:
            enabled = _select(repo_path, tools)
            monkeypatch.setattr(_server, "_client_instructions", lambda _client: 1 / 0)
            return enabled

        return select

    monkeypatch.setattr(_server, "_select_surface", broken(_server._select_surface))
    _, boot, sent = await _session(tmp_path, monkeypatch, "cursor-vscode", None)
    assert sent == boot
