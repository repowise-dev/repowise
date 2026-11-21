"""TELEMETRY.md documents every field an ``mcp_tool_call`` event may carry (#5976).

The allowlist in the savings wrapper is the only gate on what is sent, so a
field added there without a row here would leave the privacy page
under-reporting what leaves the machine.
"""

from __future__ import annotations

import re
from pathlib import Path

from repowise.server.mcp_server._savings import interaction
from repowise.server.mcp_server._savings.wrapper import MCP_TOOL_CALL_FIELDS

TELEMETRY_DOC = Path(__file__).resolve().parents[3] / "docs" / "reference" / "TELEMETRY.md"
STAGE_ROW = "properties.<stage>_ms"


def _doc() -> str:
    return TELEMETRY_DOC.read_text(encoding="utf-8")


def _mcp_tool_call_rows() -> dict[str, str]:
    """``{field: row}`` for the table under the ``mcp_tool_call`` heading."""
    text = _doc()
    section = text.split("### `mcp_tool_call`", 1)[1].split("\n### ", 1)[0]
    rows = {}
    for line in section.splitlines():
        match = re.match(r"\|\s*`properties\.([^`]+)`", line.strip())
        if match:
            rows[match.group(1)] = line
    return rows


def test_every_allowlisted_field_has_a_row() -> None:
    rows = _mcp_tool_call_rows()
    text = _doc()
    stage_fields = {f"{stage}_ms" for stage in interaction.STAGES}
    # A field every event carries (``client``) is documented once, above the tables.
    missing = sorted(
        f
        for f in MCP_TOOL_CALL_FIELDS - stage_fields
        if f not in rows and f"Every event's `properties.{f}`" not in text
    )
    assert missing == [], f"MCP_TOOL_CALL_FIELDS has no TELEMETRY.md row for {missing}"


def test_the_stage_row_names_every_timed_stage() -> None:
    row = _mcp_tool_call_rows().get(STAGE_ROW.removeprefix("properties."))
    assert row is not None, f"TELEMETRY.md has no `{STAGE_ROW}` row"
    stage_fields = {f for f in MCP_TOOL_CALL_FIELDS if f.endswith("_ms") and f != "duration_ms"}
    assert stage_fields == {f"{stage}_ms" for stage in interaction.STAGES}
    unnamed = sorted(stage for stage in interaction.STAGES if f"`{stage}`" not in row)
    assert unnamed == [], f"the `{STAGE_ROW}` row does not name {unnamed}"
