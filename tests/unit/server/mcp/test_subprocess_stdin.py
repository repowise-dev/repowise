"""Every child process an MCP tool can spawn must not inherit stdin.

Inside a stdio MCP server stdin is the JSON-RPC pipe; a child that reads it
consumes the protocol stream.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

_MODULES = (
    "repowise.server.mcp_server._code_rationale",
    "repowise.server.mcp_server.tool_answer.data_shape",
    "repowise.core.analysis.history_scan",
    "repowise.core.precedent.structural",
    "repowise.core.analysis.decisions.accepter",
    "repowise.core.analysis.decisions.source_files",
    "repowise.core.procutils",
    "repowise.core.providers.llm.codex_cli",
    "repowise.core.providers.llm.opencode",
)
_SPAWNERS = {"run", "Popen", "check_output", "check_call", "call"}


@pytest.mark.parametrize("module", _MODULES)
def test_subprocess_calls_pass_stdin(module):
    path = Path(importlib.import_module(module).__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    missing = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _SPAWNERS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
        and not {k.arg for k in node.keywords} & {"stdin", "input"}
    ]
    assert not missing, f"{path.name}: subprocess call without stdin at lines {missing}"
