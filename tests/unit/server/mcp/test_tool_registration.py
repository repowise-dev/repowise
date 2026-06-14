"""MCP server tool registration."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import repowise.server.mcp_server as mcp_server
from repowise.core.registry import mcp_tool_registry
from repowise.server.mcp_server import create_mcp_server


def _is_tool_decorator(decorator: ast.expr) -> bool:
    if isinstance(decorator, ast.Call):
        decorator = decorator.func
    return isinstance(decorator, ast.Attribute) and decorator.attr == "tool"


def _declared_tool_names() -> set[str]:
    base = Path(mcp_server.__file__).parent
    names: set[str] = set()

    for path in base.rglob("*.py"):
        if not any(part.startswith("tool_") for part in path.relative_to(base).parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and any(
                _is_tool_decorator(decorator) for decorator in node.decorator_list
            ):
                names.add(node.name)

    return names


@pytest.mark.asyncio
async def test_mcp_server_exposes_all_registered_tools() -> None:
    tools = await create_mcp_server().list_tools()
    declared_tool_names = _declared_tool_names()
    registered_tool_names = {tool.__name__ for tool in mcp_tool_registry.tools()}
    exposed_tool_names = {tool.name for tool in tools}

    assert registered_tool_names == declared_tool_names
    assert exposed_tool_names == registered_tool_names
