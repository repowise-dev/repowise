"""The instructions and ``tools/list`` a server with no checkout can send.

A host that answers from a stored index cannot keep the promises the default
words make (``lines`` read from live files, a verified edit set, uncommitted
work). ``no_checkout=True`` rewords those parts; the default output stays
byte for byte what it was, and the reworded output is never larger.
"""

from __future__ import annotations

import json

import pytest

from repowise.core.registry.instructions import (
    CHECK_CLAUSES,
    NO_CHECKOUT_CHECK_CLAUSES,
    NO_CHECKOUT_TOOL_LINES,
    NO_INDEX_LINE,
    TOOL_LINES,
    server_instructions,
)
from repowise.server.mcp_server import tool_definitions
from repowise.server.mcp_server._no_checkout import NO_CHECKOUT_WORDING

_ALL = sorted({*TOOL_LINES, *CHECK_CLAUSES, "get_overview", "get_health", "get_dead_code"})
#: Words a server with no checkout cannot stand behind.
_CHECKOUT_PROMISES = (
    "live files",
    "uncommitted",
    "verified, line-numbered",
    "line-verified",
    "live range",
    "the whole edit set",
    "tree is clean",
    "repowise update",
    "repowise init",
)


def _size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


@pytest.mark.parametrize("served", [None, _ALL, ["get_answer"], ["search_codebase", "get_why"]])
@pytest.mark.parametrize("workspace", [False, True])
def test_default_instructions_are_unchanged_and_the_variant_is_no_larger(served, workspace):
    default = server_instructions(served, workspace=workspace, repo_line="why line")
    assert default == server_instructions(
        served, workspace=workspace, repo_line="why line", no_checkout=False
    )
    for line in (*NO_CHECKOUT_TOOL_LINES.values(), *NO_CHECKOUT_CHECK_CLAUSES.values()):
        assert line not in default
    variant = server_instructions(
        served, workspace=workspace, repo_line="why line", no_checkout=True
    )
    assert len(variant.encode()) <= len(default.encode())


def test_no_checkout_instructions_reword_the_checkout_lines():
    text = server_instructions(None, no_checkout=True, extra=["host line"])

    assert NO_CHECKOUT_TOOL_LINES["search_codebase"] in text
    assert NO_CHECKOUT_TOOL_LINES["get_context"] in text
    assert NO_CHECKOUT_CHECK_CLAUSES["get_change_risk"] in text
    assert NO_INDEX_LINE not in text
    assert not [w for w in _CHECKOUT_PROMISES if w in text]
    # Engine lines in their usual place, host lines last.
    assert text.index("search_codebase") < text.index("get_change_risk") < text.index("host line")
    assert text.endswith("host line")
    # A tool the server does not serve is still never named.
    assert "get_change_risk" not in server_instructions(["get_answer"], no_checkout=True)


def test_every_no_checkout_phrase_still_matches_the_default_definitions():
    """A docstring edit that drops a phrase must update the wording table too."""
    defaults = {d["name"]: d for d in tool_definitions(NO_CHECKOUT_WORDING, workspace=False)}
    assert set(defaults) == set(NO_CHECKOUT_WORDING)
    for name, swaps in NO_CHECKOUT_WORDING.items():
        props = defaults[name]["inputSchema"].get("properties", {})
        for where, old, _new in swaps:
            holder = defaults[name] if where == "description" else props[where]
            assert old in holder["description"], (name, where, old)


@pytest.mark.parametrize("workspace", [False, True])
def test_no_checkout_definitions_change_only_the_checkout_words(workspace):
    from repowise.core.registry import mcp_tool_registry
    from repowise.server.mcp_server import _register_tools

    _register_tools()
    names = sorted({entry.name for entry in mcp_tool_registry.entries()})
    default = tool_definitions(names, workspace=workspace)
    assert json.dumps(default) == json.dumps(
        tool_definitions(names, workspace=workspace, no_checkout=False)
    )
    variant = tool_definitions(names, workspace=workspace, no_checkout=True)

    assert [d["name"] for d in variant] == [d["name"] for d in default]
    for before, after in zip(default, variant, strict=True):
        if before["name"] not in NO_CHECKOUT_WORDING:
            assert after == before
            continue
        assert after != before
        assert {k: v for k, v in after.items() if k not in ("description", "inputSchema")} == {
            k: v for k, v in before.items() if k not in ("description", "inputSchema")
        }
        assert _size(after) <= _size(before)
        promised = json.dumps(after)
        assert not [w for w in _CHECKOUT_PROMISES if w in promised], before["name"]
    assert _size(variant) < _size(default)
    # The variant rewords a fresh dump: a default call after it is unchanged.
    assert json.dumps(tool_definitions(names, workspace=workspace)) == json.dumps(default)
