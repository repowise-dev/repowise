"""docs/agent/MCP_TOOLS.md must track the live MCP tool registry.

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

DOC = Path(__file__).parents[4] / "docs" / "agent" / "MCP_TOOLS.md"


@pytest.fixture(scope="module")
def doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def _entries():
    # Tool modules import lazily, so importing the package alone no longer
    # registers anything — the surface has to be asked for explicitly.
    from repowise.server.mcp_server import ensure_full_surface

    ensure_full_surface()
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
    workspace_default = sum(1 for e in entries if e.default)
    opt_in = sum(1 for e in entries if not e.default)

    m = re.search(
        r"(\d+) tools are registered in total\. A single-repo server advertises "
        r"(\d+) by default: exactly the canonical tools\. Workspace mode adds .*?, for "
        r"(\d+)\. (\d+) specialist tools are opt-in",
        doc_text,
        flags=re.DOTALL,
    )
    assert m, "MCP_TOOLS.md surface-count paragraph not found (wording changed?)"
    assert [int(g) for g in m.groups()] == [
        total,
        single_default,
        workspace_default,
        opt_in,
    ], "MCP_TOOLS.md surface counts drifted from the registry"


def _inventory_names(doc_text: str, heading: str) -> set[str]:
    match = re.search(rf"\*\*{re.escape(heading)}[^\n]*\*\*\n(.*?)(?=\n\n)", doc_text, re.DOTALL)
    assert match, f"missing inventory heading: {heading}"
    return set(re.findall(r"\[([a-z_]+)\]\(#", match.group(1)))


def test_inventory_tiers_and_mode_eligibility_match_registry(doc_text: str):
    entries = _entries()
    canonical = {entry.name for entry in entries if entry.tier == "canonical"}
    utilities = {entry.name for entry in entries if entry.tier == "utility"}
    specialists = {entry.name for entry in entries if entry.tier == "specialist"}
    assert _inventory_names(doc_text, "Canonical tools") == canonical
    assert _inventory_names(doc_text, "Workspace discovery utility") == utilities
    assert _inventory_names(doc_text, "Opt-in specialists") == specialists

    assert all(entry.default for entry in entries if entry.tier in {"canonical", "utility"})
    assert all(not entry.default for entry in entries if entry.tier == "specialist")
    assert all(entry.requires_workspace for entry in entries if entry.tier == "utility")


def test_lean_profile_paragraph_names_the_lean_tools(doc_text: str):
    m = re.search(r"\*\*The `lean` profile\*\*.*?(?=\n\n)", doc_text, flags=re.DOTALL)
    assert m, "MCP_TOOLS.md lean-profile paragraph not found"
    paragraph = m.group(0)
    missing = {name for name in LEAN_TOOLS if f"`{name}`" not in paragraph}
    assert not missing, f"lean tools missing from the profile paragraph: {missing}"
    assert "`list_repos`" in paragraph
