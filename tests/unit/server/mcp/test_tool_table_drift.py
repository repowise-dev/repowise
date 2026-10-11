"""The CLAUDE.md tool table must track the live MCP tool registry.

The table used to be hand-edited prose inside claude_md.j2 and silently
drifted from the registered surface. It now lives in
``editor_files/tool_table.py``; this test pins it to the registry.
"""

from __future__ import annotations

from repowise.core.generation.editor_files.tool_table import (
    TOOL_TABLE_ROWS,
    render_tool_table,
)
from repowise.core.registry import mcp_tool_registry


def _registered_names() -> set[str]:
    # Tool modules import lazily; the surface has to be asked for.
    from repowise.server.mcp_server import ensure_full_surface

    ensure_full_surface()
    return {fn.__name__ for fn in mcp_tool_registry.tools()}


def test_every_table_row_names_a_registered_tool():
    unknown = set(TOOL_TABLE_ROWS) - _registered_names()
    assert not unknown, f"tool_table.py rows for unregistered tools: {unknown}"


def test_agent_table_is_exactly_the_registry_canonical_tier():
    from repowise.server.mcp_server import ensure_full_surface

    ensure_full_surface()
    canonical = {entry.name for entry in mcp_tool_registry.entries() if entry.tier == "canonical"}
    assert set(TOOL_TABLE_ROWS) == canonical


def test_render_produces_one_row_per_tool():
    md = render_tool_table()
    assert md.startswith("| Tool | When and why |")
    # Header + separator + one line per row.
    assert len(md.splitlines()) == 2 + len(TOOL_TABLE_ROWS)


#: Longest a single row's prose may be, and the mean across all rows. This table
#: is resident in the prompt prefix of every session in every indexed repo, so
#: it is re-read on every API call (measured at 50.4x on one corpus). It reached
#: 3,402 characters by one reasonable-looking sentence at a time and was cut to
#: ~1,850 in 2026-08; the budget exists so that regrowth is a decision somebody
#: makes on purpose rather than a drift nobody measures.
#:
#: **Both bounds are needed and the per-row one alone is not enough.** The drift
#: this guards against adds a 30 to 60 character sentence at a time, spread over
#: ten rows, which a per-row ceiling set above the current maximum absorbs
#: completely. The mean is what actually tracks the total, and it is expressed
#: as a mean rather than a total so that adding a tool does not fail the build.
#: Current values: max 179, mean ~145.
_MAX_ROW_CHARS = 200
_MAX_MEAN_ROW_CHARS = 165


def test_no_row_outgrows_its_budget():
    oversized = {
        name: len(row)
        for name, (_signature, row) in TOOL_TABLE_ROWS.items()
        if len(row) > _MAX_ROW_CHARS
    }
    assert not oversized, (
        f"tool table rows over {_MAX_ROW_CHARS} chars: {oversized}. This text is "
        "resident in every session's prompt prefix; put reference detail in the "
        "tool's own schema description or docs/agent/MCP_TOOLS.md instead."
    )


def test_the_table_as_a_whole_does_not_creep():
    lengths = [len(row) for _signature, row in TOOL_TABLE_ROWS.values()]
    mean = sum(lengths) / len(lengths)
    assert mean <= _MAX_MEAN_ROW_CHARS, (
        f"mean tool-table row is {mean:.0f} chars, over the {_MAX_MEAN_ROW_CHARS} "
        "budget. A sentence added to every row costs more than any single row "
        "ever will; this is the bound that catches that."
    )
