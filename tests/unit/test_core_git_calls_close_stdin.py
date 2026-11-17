"""Every ``git`` child started from ``repowise.core`` gets its own stdin.

A child started without ``stdin=`` inherits the parent's stdin. Under the stdio
MCP server that is the JSON-RPC pipe, and a git child holding that handle can
wait on it until its timeout expires (see ``analysis/git_cli._git``). So each
``subprocess`` call whose command starts with ``"git"`` must pass ``stdin=`` or
``input=``.

Ceiling: the command is matched only when it is a list literal starting with the
string ``"git"``; a command built in a variable is not seen.
"""

from __future__ import annotations

import ast
import pathlib

import repowise.core as core

_CORE = pathlib.Path(core.__file__).parent
_FUNCS = frozenset({"run", "Popen", "call", "check_call", "check_output"})


def _git_calls_without_stdin(path: pathlib.Path) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _FUNCS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.args
            and isinstance(node.args[0], ast.List)
            and node.args[0].elts
            and isinstance(node.args[0].elts[0], ast.Constant)
            and node.args[0].elts[0].value == "git"
        ):
            continue
        if not {"stdin", "input"} & {kw.arg for kw in node.keywords}:
            lines.append(node.lineno)
    return lines


def test_core_git_calls_do_not_inherit_stdin() -> None:
    offenders = [
        f"{path.relative_to(_CORE)}:{line}"
        for path in sorted(_CORE.rglob("*.py"))
        for line in _git_calls_without_stdin(path)
    ]
    assert offenders == [], "git calls that inherit stdin: " + ", ".join(offenders)
