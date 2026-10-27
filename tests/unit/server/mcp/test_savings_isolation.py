"""A test must not bank a saving in the developer's own ledger.

The canonical savings event goes to whatever repository the budget layer
resolved, and that resolution falls back to the current working directory,
which under pytest is the checkout itself. Tests driving ``tool_middleware``
wrote rows into the real repository's sidecar, where they look like live
traffic and are recognisable only by their synthetic tool names.
"""

from __future__ import annotations

import pytest

from repowise.server.mcp_server import tool_middleware


@pytest.mark.asyncio
async def test_a_tool_call_in_a_test_banks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[object] = []
    from repowise.core.savings import repository

    monkeypatch.setattr(
        repository.SavingsRepository,
        "record_event",
        lambda self, event: written.append(event) or True,
    )

    async def odd_tool(x: int) -> dict:
        return {"value": x, "_meta": {}}

    result = await tool_middleware(odd_tool)(1)

    assert result["value"] == 1
    assert written == []
