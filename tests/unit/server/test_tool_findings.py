"""set_finding_status (MCP) records dispositions through the shared writer.

The CRUD lifecycle is pinned in test_refactoring_lifecycle.py; these tests
cover the tool layer: it validates the vocabulary, resolves the plan by
storage id or display id, routes through update_refactoring_suggestion_status
(the single writer), and surfaces the resulting status.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from repowise.server.mcp_server.tool_findings import ALLOWED_STATUSES, set_finding_status


class _Row:
    def __init__(self, status: str = "false_positive", status_reason: str = "agent"):
        self.id = "row-1"
        self.public_id = "plan-1"
        self.status = status
        self.status_reason = status_reason
        self.status_changed_at = None


class _Repo:
    id = "repo-1"
    name = "acme"


@pytest.fixture
def ctx():
    """Fake _resolve_repo_context result + session plumbing."""
    fake_session = AsyncMock()
    fake_session.__aenter__.return_value = fake_session
    fake_session.__aexit__.return_value = False

    fake_ctx = type(
        "Ctx",
        (),
        {"session_factory": object(), "alias": "acme", "path": "/tmp/acme"},
    )()

    with (
        patch(
            "repowise.server.mcp_server.tool_findings._resolve_repo_context",
            new=AsyncMock(return_value=fake_ctx),
        ),
        patch(
            "repowise.server.mcp_server.tool_findings.get_session",
            side_effect=lambda _f: fake_session,
        ),
        patch(
            "repowise.server.mcp_server.tool_findings._get_repo",
            new=AsyncMock(return_value=_Repo()),
        ),
    ):
        yield fake_session


async def test_unknown_status_raises(ctx):
    with pytest.raises(ValueError, match=r"unknown finding status: 'bogus'"):
        await set_finding_status("row-1", "bogus")


async def test_missing_plan_raises(ctx):
    with patch(
        "repowise.server.mcp_server.tool_findings.get_refactoring_suggestion",
        new=AsyncMock(return_value=None),
    ), patch(
        "repowise.server.mcp_server.tool_findings.get_refactoring_suggestions",
        new=AsyncMock(return_value=[]),
    ):
        with pytest.raises(ValueError, match="refactoring plan not found"):
            await set_finding_status("absent", "resolved")


async def test_write_goes_through_the_shared_writer(ctx):
    """The tool must route through update_refactoring_suggestion_status — the
    same writer the REST PATCH and the pipeline use — so a false_positive
    recorded here is suppressed by the finalizer the same way."""
    update = AsyncMock(return_value=_Row())
    with patch(
        "repowise.server.mcp_server.tool_findings.get_refactoring_suggestion",
        new=AsyncMock(return_value=_Row()),
    ), patch(
        "repowise.server.mcp_server.tool_findings.update_refactoring_suggestion_status",
        update,
    ):
        out = await set_finding_status("row-1", "false_positive", reason="dup of #12")

    update.assert_awaited_once()
    call = update.await_args.args
    assert call[1] == "repo-1"  # repo id
    assert call[2] == "row-1"  # row id
    assert call[3] == "false_positive"
    assert update.await_args.kwargs["reason"] == "dup of #12"
    assert out["status"] == "false_positive"
    assert out["id"] == "row-1"
    assert "not be re-emitted" in out["note"]


async def test_display_id_fallback(ctx):
    """A deep link may carry the display id (\"<alias> <file>:<symbol>\")
    rather than a storage or content id; the tool falls back like
    generate_refactoring_code does."""
    matched = object()
    with patch(
        "repowise.server.mcp_server.tool_findings.get_refactoring_suggestion",
        new=AsyncMock(return_value=None),
    ), patch(
        "repowise.server.mcp_server.tool_findings.get_refactoring_suggestions",
        new=AsyncMock(return_value=[_Row()]),
    ), patch(
        "repowise.server.mcp_server.tool_health._refactoring_plan_id",
        new=lambda _c, _alias: "acme a.py:Foo",
    ), patch(
        "repowise.server.mcp_server.tool_findings.update_refactoring_suggestion_status",
        new=AsyncMock(return_value=_Row(status="acknowledged")),
    ):
        out = await set_finding_status("acme a.py:Foo", "acknowledged")

    assert out["status"] == "acknowledged"
