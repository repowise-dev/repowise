"""Tool docstrings ARE the agent-facing schema — keep them lean and legible.

Every registered MCP tool's docstring ships verbatim inside the tool schema
that agent harnesses load (and may truncate). A docstring past ~400 tokens
risks arriving cut off mid-sentence, and pushes schema-loading cost onto
every session. The reference-manual detail belongs in docs/MCP_TOOLS.md.
"""

from __future__ import annotations

import inspect

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


def _schema_doc(fn) -> str:
    """The docstring as measured, normalized so the verdict is reproducible.

    ``@mcp.tool()`` does not hand back the same string on every version of the
    ``mcp`` package: 1.27 runs the docstring through ``inspect.cleandoc`` while
    1.26 (the locked version) leaves each line's source indentation on it. That
    is a ~90 char swing on a long docstring, which was enough for this budget to
    pass locally and fail in CI on identical source. Dedent here so the number
    measures the prose an agent reads rather than the dependency's normalizing
    behaviour, and so a developer's environment cannot be more lenient than CI.
    """
    return inspect.cleandoc(fn.__doc__ or "")


def test_every_tool_docstring_fits_the_schema_budget():
    oversized = {
        fn.__name__: len(_schema_doc(fn))
        for fn in _registered_tools()
        if len(_schema_doc(fn)) > _MAX_DOCSTRING_CHARS
    }
    assert not oversized, (
        f"Tool docstrings over {_MAX_DOCSTRING_CHARS} chars (~400 tokens): "
        f"{oversized}. Trim the docstring; move reference detail to "
        "docs/MCP_TOOLS.md."
    )


def test_every_tool_docstring_leads_with_a_task_shaped_line():
    for fn in _registered_tools():
        doc = _schema_doc(fn).strip()
        assert doc, f"{fn.__name__} has no docstring — its schema would be blank"
        first = doc.splitlines()[0].strip()
        assert len(first) <= _MAX_FIRST_LINE_CHARS, (
            f"{fn.__name__} first docstring line is {len(first)} chars; keep the "
            "when-to-call summary scannable."
        )
