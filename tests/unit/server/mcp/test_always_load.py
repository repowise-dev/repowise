"""``mcp.always_load`` marks the core read tools as always loaded in tools/list.

Claude Code reads ``_meta: {"anthropic/alwaysLoad": true}`` on a tool entry and
keeps that schema loaded instead of deferring it behind tool search. Off (the
default), tools/list is exactly what it was without the setting.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp.types import Implementation

from repowise.core.registry.tool_selection import ALWAYS_LOAD_TOOLS

_HINT = {"anthropic/alwaysLoad": True}


async def _wire(
    tmp_path: Any, monkeypatch: Any, config: str | None, tools: Any = None
) -> list[dict]:
    """The tools/list entries as Claude Code receives them."""
    from mcp.shared.memory import create_connected_server_and_client_session

    from repowise.server.mcp_server import _state, _tool_selection, create_mcp_server, mcp

    monkeypatch.setattr(_tool_selection, "_is_workspace", lambda _path: False)
    for name, value in list(vars(_state).items()):
        if name.startswith("_") and not name.startswith("__") and not callable(value):
            monkeypatch.setattr(_state, name, value)
    monkeypatch.setattr(mcp._mcp_server, "instructions", mcp.instructions)
    if config is not None:
        (tmp_path / ".repowise").mkdir(exist_ok=True)
        (tmp_path / ".repowise" / "config.yaml").write_text(config, encoding="utf-8")
    try:
        server = create_mcp_server(str(tmp_path), tools=tools)
        async with create_connected_server_and_client_session(
            server._mcp_server, client_info=Implementation(name="claude-code", version="1")
        ) as session:
            listed = (await session.list_tools()).tools
        return [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in listed]
    finally:
        if _tool_selection._full_surface is not None:
            mcp._tool_manager._tools = dict(_tool_selection._full_surface)
        _tool_selection._selected_surface = None
        _tool_selection._boot_inputs = None
        _tool_selection._always_load = frozenset()


@pytest.mark.asyncio
async def test_on_marks_exactly_the_core_read_tools(tmp_path, monkeypatch) -> None:
    wire = await _wire(tmp_path, monkeypatch, "mcp:\n  always_load: true\n")
    marked = {t["name"] for t in wire if t.get("_meta") == _HINT}
    assert marked == {"get_answer", "get_context", "get_symbol", "search_codebase"}
    assert marked == ALWAYS_LOAD_TOOLS
    # Every other listed tool (get_why among them) stays deferred.
    assert all("_meta" not in t for t in wire if t["name"] not in marked)
    assert "get_why" in {t["name"] for t in wire}


@pytest.mark.asyncio
@pytest.mark.parametrize("config", [None, "mcp:\n  always_load: false\n"])
async def test_off_lists_tools_unchanged(tmp_path, monkeypatch, config) -> None:
    from repowise.server.mcp_server import _tool_selection

    wire = await _wire(tmp_path, monkeypatch, config)
    assert all("_meta" not in t for t in wire)

    # Byte-identical to the list without the hint step at all.
    monkeypatch.setattr(_tool_selection, "mark_always_loaded", lambda tools: tools)
    plain = await _wire(tmp_path, monkeypatch, config)
    assert json.dumps(wire) == json.dumps(plain)


@pytest.mark.asyncio
async def test_unserved_tools_are_not_listed_or_marked(tmp_path, monkeypatch) -> None:
    wire = await _wire(
        tmp_path, monkeypatch, "mcp:\n  always_load: true\n", tools="get_answer,get_why"
    )
    assert {t["name"]: t.get("_meta") for t in wire} == {"get_answer": _HINT, "get_why": None}
