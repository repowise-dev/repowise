"""Every advertised tool reports how long it took."""

from __future__ import annotations

from typing import Any

import pytest

from repowise.server.mcp_server import tool_middleware


async def _run(fn: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    return await tool_middleware(fn)(*args, **kwargs)


async def test_timing_stamped_for_a_tool_that_does_not_thread_it() -> None:
    async def get_risk(targets: list[str], repo: str | None = None) -> dict[str, Any]:
        return {"targets": {}, "_meta": {"contract_version": 1}}

    result = await _run(get_risk, ["src/a.py"])

    assert isinstance(result["_meta"]["timing_ms"], float)
    assert result["_meta"]["timing_ms"] >= 0


async def test_a_tool_that_reports_its_own_timing_keeps_it() -> None:
    async def get_answer(question: str) -> dict[str, Any]:
        return {"answer": "yes", "_meta": {"contract_version": 1, "timing_ms": 1234.5}}

    result = await _run(get_answer, "why?")

    # Preserved, not replaced by the middleware's own near-zero elapsed. The
    # quantize layer rounds it, so compare with a tolerance.
    assert result["_meta"]["timing_ms"] == pytest.approx(1234.5, abs=1)


async def test_timing_survives_the_final_budget_pass() -> None:
    async def get_why(query: str | None = None) -> dict[str, Any]:
        return {
            "mode": "search",
            "decisions": [{"id": i, "body": "x" * 2000} for i in range(60)],
            "_meta": {"contract_version": 1},
        }

    result = await _run(get_why, "why is auth shaped this way?")

    assert result["_meta"]["timing_ms"] is not None
    budget = result["_meta"]["response_budget"]
    assert budget["serialized_chars"] <= budget["limit_chars"]


@pytest.mark.parametrize("returned", [None, [], "not a dict"])
async def test_non_dict_results_are_passed_through(returned: Any) -> None:
    async def odd_tool() -> Any:
        return returned

    assert await _run(odd_tool) == returned
