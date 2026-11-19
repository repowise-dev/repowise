"""The tool schemas hosts re-send every turn carry nothing that restates them.

Arguments are validated against each tool's function signature, so a
parameter left out of the advertised schema keeps working for a caller that
passes it.
"""

from __future__ import annotations

import pytest

from repowise.core.registry.tool_selection import CORE_TOOLS
from repowise.server.mcp_server import (
    _UNADVERTISED_PARAMS,
    _collapse_optional,
    _lift_args,
    ensure_full_surface,
)


async def _listed() -> dict:
    return {t.name: t for t in await ensure_full_surface().list_tools()}


@pytest.mark.asyncio
async def test_no_optional_parameter_carries_a_null_branch():
    for tool in (await _listed()).values():
        for name, prop in tool.inputSchema.get("properties", {}).items():
            assert {"type": "null"} not in prop.get("anyOf", []), (tool.name, name)


@pytest.mark.asyncio
async def test_unadvertised_parameters_are_unlisted_but_still_accepted():
    listed = await _listed()
    manager = ensure_full_surface()._tool_manager
    for tool_name, hidden in _UNADVERTISED_PARAMS.items():
        schema = listed[tool_name].inputSchema
        fields = manager.get_tool(tool_name).fn_metadata.arg_model.model_fields
        for name in hidden:
            assert name in fields, f"{tool_name} has no parameter {name}"
            assert not fields[name].is_required(), (tool_name, name)
            assert name not in schema["properties"], (tool_name, name)


@pytest.mark.asyncio
async def test_core_tools_document_parameters_on_the_schema():
    listed = await _listed()
    for name in CORE_TOOLS:
        tool = listed[name]
        assert "Args:" not in tool.description, name
        props = tool.inputSchema["properties"]
        undocumented = [p for p, s in props.items() if "description" not in s]
        assert not undocumented, (name, undocumented)


def test_lift_args_moves_entries_and_keeps_later_sections():
    props = {"a": {"type": "string"}, "b": {"type": "string"}, "c": {"type": "integer"}}
    doc = (
        "Lead line.\n\nBody.\n\nArgs:\n    a, b: Shared\n        text.\n"
        "    gone: Not a property.\n    c: Count.\n\nReturns:\n    A dict.\n"
    )

    lifted = _lift_args(doc, props)

    assert lifted == "Lead line.\n\nBody.\n\nReturns:\n    A dict."
    assert props["a"]["description"] == props["b"]["description"] == "Shared text."
    assert props["c"]["description"] == "Count."


def test_lift_args_leaves_a_description_without_args_alone():
    props = {"a": {"type": "string"}}
    assert _lift_args("Only prose.", props) == "Only prose."
    assert props == {"a": {"type": "string"}}


def test_collapse_optional_keeps_every_non_null_variant():
    single = {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "x"}
    union = {"anyOf": [{"type": "string"}, {"items": {"type": "string"}, "type": "array"}, {"type": "null"}]}

    _collapse_optional(single)
    _collapse_optional(union)

    assert single == {"type": "string", "description": "x"}
    assert union == {"anyOf": [{"type": "string"}, {"items": {"type": "string"}, "type": "array"}]}
