"""Tool docstrings ARE the agent-facing schema — keep them lean and legible.

Every registered MCP tool's docstring ships verbatim inside the tool schema
that agent harnesses load (and may truncate). A docstring past ~400 tokens
risks arriving cut off mid-sentence, and pushes schema-loading cost onto
every session. The reference-manual detail belongs in docs/agent/MCP_TOOLS.md.
"""

from __future__ import annotations

from repowise.core.registry import mcp_tool_registry

# ~400 tokens at the 4-chars/token estimate used across the codebase.
_MAX_DOCSTRING_CHARS = 1600
# The first line is what a scanning agent reads when picking a tool.
_MAX_FIRST_LINE_CHARS = 90


def _registered_tools():
    # Importing the package registers every @mcp.tool with the registry.
    import repowise.server.mcp_server  # noqa: F401

    tools = mcp_tool_registry.tools()
    assert tools, "tool registry is empty — import side effect broken?"
    return tools


def test_every_tool_docstring_fits_the_schema_budget():
    oversized = {
        fn.__name__: len(fn.__doc__ or "")
        for fn in _registered_tools()
        if len(fn.__doc__ or "") > _MAX_DOCSTRING_CHARS
    }
    assert not oversized, (
        f"Tool docstrings over {_MAX_DOCSTRING_CHARS} chars (~400 tokens): "
        f"{oversized}. Trim the docstring; move reference detail to "
        "docs/agent/MCP_TOOLS.md."
    )


def test_every_tool_docstring_leads_with_a_task_shaped_line():
    for fn in _registered_tools():
        doc = (fn.__doc__ or "").strip()
        assert doc, f"{fn.__name__} has no docstring — its schema would be blank"
        first = doc.splitlines()[0].strip()
        assert len(first) <= _MAX_FIRST_LINE_CHARS, (
            f"{fn.__name__} first docstring line is {len(first)} chars; keep the "
            "when-to-call summary scannable."
        )
