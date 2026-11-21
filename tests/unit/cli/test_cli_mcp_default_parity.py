"""A CLI adapter run with no flags makes the MCP tool's own default call.

``repowise search`` and ``repowise symbol`` are thin adapters over
``search_codebase`` and ``get_symbol``. Their ``--full`` output is the tool's
raw dict, so the same query gives the same payload on both surfaces only when
every argument the CLI binds by default is the tool's own default. Each case
here records the arguments the command hands the tool and compares them,
defaults applied, with an MCP call that passes only the query.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect

import pytest
from click.testing import CliRunner

from repowise.cli.commands import _tool_adapters as _ta
from repowise.cli.commands.search_cmd import search_command
from repowise.cli.commands.symbol_cmd import symbol_command


def _bound(tool, **kwargs) -> dict:
    bound = inspect.signature(tool).bind(**kwargs)
    bound.apply_defaults()
    return dict(bound.arguments)


@pytest.mark.parametrize(
    ("command", "args", "module", "name", "mcp_call"),
    [
        (
            search_command,
            ["persist_result"],
            "repowise.server.mcp_server.tool_search",
            "search_codebase",
            {"query": "persist_result"},
        ),
        (
            symbol_command,
            ["a.py::f"],
            "repowise.server.mcp_server.tool_symbol",
            "get_symbol",
            {"symbol_id": "a.py::f"},
        ),
    ],
)
def test_the_default_cli_call_is_the_default_mcp_call(
    monkeypatch, tmp_path, command, args, module, name, mcp_call
):
    (tmp_path / ".repowise").mkdir()
    tool = getattr(importlib.import_module(module), name)
    seen: list[dict] = []

    async def _record(**kwargs):
        seen.append(_bound(tool, **kwargs))
        return {"_meta": {}}

    def _run(repo_path, factory, tool_name, **_kwargs):
        return asyncio.run(factory())

    monkeypatch.setattr(importlib.import_module(module), name, _record)
    monkeypatch.setattr(_ta, "run", _run)
    target = ["--path", str(tmp_path)] if command is symbol_command else [str(tmp_path)]
    result = CliRunner().invoke(
        command, [*args, *target, "--no-workspace", "--full"]
    )

    assert result.exit_code == 0, result.output
    assert seen == [_bound(tool, **mcp_call)]


def test_search_prompt_describes_fulltext_as_the_fused_search():
    """``fulltext`` and ``semantic`` alias the same tool mode, so the agent
    prompt must not describe ``fulltext`` as keyword-only."""
    from pathlib import Path

    from repowise.cli.commands.search_cmd import _MODE_ALIASES

    assert _MODE_ALIASES["fulltext"] == _MODE_ALIASES["semantic"]
    root = Path(__file__).resolve().parents[3]
    lines = (root / "plugins" / "shared" / "commands" / "search.md").read_text(
        encoding="utf-8"
    ).splitlines()
    fulltext = next(line for line in lines if line.startswith("- `--mode fulltext`"))
    assert "fused keyword and semantic" in fulltext
