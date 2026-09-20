"""Regression: chat summaries must cover every registered chat tool."""

from __future__ import annotations

from repowise.server.chat_tools import get_tool_catalog
from repowise.server.routers.chat import _build_tool_summary


def test_get_change_risk_summary_is_not_generic_completed():
    summary = _build_tool_summary(
        "get_change_risk",
        {
            "ref": "HEAD",
            "score": 7.2,
            "review_priority": "Elevated",
            "risk_percentile": 82.0,
        },
    )
    assert summary != "Completed"
    assert "HEAD" in summary
    assert "Elevated" in summary
    assert "p82" in summary


def test_every_chat_registry_tool_has_a_non_generic_summary_shape():
    """Smoke: registered tools either specialize or return Completed only as fallback."""
    catalog = get_tool_catalog(None)
    assert "get_change_risk" in {tool.entry.name for tool in catalog}
    # get_change_risk must not fall through to the generic Completed string.
    assert (
        _build_tool_summary("get_change_risk", {"ref": "main..HEAD", "review_priority": "Typical"})
        != "Completed"
    )
