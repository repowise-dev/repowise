"""The budget layer's accounting rides on a response only when it carries news."""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest

from repowise.server.mcp_server import tool_middleware
from repowise.server.mcp_server._budget import enforce_response_budget
from repowise.server.mcp_server._meta import DEBUG_META_ENV


def get_why(query: str | None = None) -> None:
    pass


def _enforce(result: dict[str, Any]) -> dict[str, Any]:
    return enforce_response_budget(
        "get_why", result, signature=inspect.signature(get_why), args=(), kwargs={}
    )


def _oversize() -> dict[str, Any]:
    return {
        "mode": "search",
        "decisions": [{"id": i, "body": "x" * 2000} for i in range(60)],
        "_meta": {},
    }


def test_an_untouched_response_carries_no_accounting() -> None:
    result = _enforce({"mode": "search", "decisions": [], "_meta": {}})

    assert "response_budget" not in result["_meta"]
    assert "completeness" not in result["_meta"]


def test_a_trimmed_response_carries_exact_accounting() -> None:
    result = _enforce(_oversize())

    budget = result["_meta"]["response_budget"]
    assert budget["serialized_chars"] == len(json.dumps(result, separators=(",", ":")))
    assert result["_meta"]["completeness"]["capped"] is True


async def test_the_outer_pass_keeps_what_the_inner_pass_trimmed() -> None:
    async def get_why(query: str | None = None) -> dict[str, Any]:
        return _oversize()

    result = await tool_middleware(get_why)("why?")

    budget = result["_meta"]["response_budget"]
    assert budget["serialized_chars"] <= budget["limit_chars"]


def test_debug_meta_restores_the_accounting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEBUG_META_ENV, "1")

    result = _enforce({"mode": "search", "decisions": [], "_meta": {}})

    assert result["_meta"]["completeness"] == {"capped": False}
    assert "serialized_chars" in result["_meta"]["response_budget"]
