"""docs/MCP_TOOLS.md must track the live MCP tool registry.

The doc's tool inventory (headings) and surface counts used to be hand-edited
and could silently drift from the registered surface; this pins both, the same
way test_tool_table_drift.py pins the CLAUDE.md tool table.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from repowise.core.registry import mcp_tool_registry
from repowise.server.mcp_server._tool_selection import LEAN_TOOLS

DOC = Path(__file__).parents[4] / "docs" / "MCP_TOOLS.md"


@pytest.fixture(scope="module")
def doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def _entries():
    import repowise.server.mcp_server  # noqa: F401  (registers the tools)

    return mcp_tool_registry.entries()


def test_every_registered_tool_has_a_doc_section(doc_text: str):
    documented = set(re.findall(r"^#{2,4} `(\w+)`", doc_text, flags=re.MULTILINE))
    registered = {e.name for e in _entries()}
    missing = registered - documented
    assert not missing, f"registered tools without a MCP_TOOLS.md section: {missing}"
    ghosts = documented - registered
    assert not ghosts, f"MCP_TOOLS.md sections for unregistered tools: {ghosts}"


def test_surface_counts_match_registry(doc_text: str):
    entries = _entries()
    total = len(entries)
    single_default = sum(1 for e in entries if e.default and not e.requires_workspace)
    workspace_only = sum(1 for e in entries if e.default and e.requires_workspace)

    m = re.search(
        r"(\d+) tools are registered in total\. A single-repo server advertises "
        r"(\d+) by default.*?Workspace mode adds (\d+) more automatically, for (\d+)",
        doc_text,
        flags=re.DOTALL,
    )
    assert m, "MCP_TOOLS.md surface-count paragraph not found (wording changed?)"
    assert [int(g) for g in m.groups()] == [
        total,
        single_default,
        workspace_only,
        single_default + workspace_only,
    ], "MCP_TOOLS.md surface counts drifted from the registry"


def test_lean_profile_paragraph_names_the_lean_tools(doc_text: str):
    m = re.search(r"\*\*The `lean` profile\*\*.*?(?=\n\n)", doc_text, flags=re.DOTALL)
    assert m, "MCP_TOOLS.md lean-profile paragraph not found"
    paragraph = m.group(0)
    missing = {name for name in LEAN_TOOLS if f"`{name}`" not in paragraph}
    assert not missing, f"lean tools missing from the profile paragraph: {missing}"
    assert "`list_repos`" in paragraph
