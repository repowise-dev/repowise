"""Fix surfaces quote the tracker issue behind a fix, and stay as they were without one."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from repowise.core.issues import StoredLink
from repowise.core.persistence.crud.issue_links import store_issue_links
from repowise.core.persistence.models import FixEvent, GitMetadata, IssueLink
from repowise.server.mcp_server.tool_change_risk import _prior_fixes_block
from tests.unit.server.mcp.test_prior_fixes import _REPO_ID, _ctx_with_events

_SHA = "e" * 40
_LINK = StoredLink(
    sha=_SHA,
    key="ENG-42",
    type="bug",
    status="Done",
    title="Checkout fails on an empty cart",
    url="",
    basis="message names ENG-42",
)


@pytest.mark.asyncio
async def test_get_context_fix_history_quotes_the_linked_bug(setup_mcp, session, repo_id):
    from repowise.server.mcp_server import get_context

    row = (
        await session.execute(
            select(GitMetadata).where(GitMetadata.file_path == "src/auth/service.py")
        )
    ).scalar_one()
    row.prior_defect_count = 1
    row.last_fix_at = (row.last_commit_at or datetime.now(UTC)) - timedelta(days=3)
    session.add(
        FixEvent(
            repository_id=repo_id,
            fix_sha=_SHA,
            file_path="src/auth/service.py",
            shape_kind="code_fix",
            old_ranges_json="[]",
            committed_at=row.last_fix_at,
        )
    )
    await session.flush()

    plain = (await get_context(["src/auth/service.py"]))["targets"]["src/auth/service.py"]
    assert "linked_issues" not in plain["fix_history"]

    session.add(
        IssueLink(
            repository_id=repo_id,
            commit_sha=_SHA,
            issue_key="ENG-42",
            source="t",
            issue_type="bug",
            title="Checkout fails on an empty cart",
        )
    )
    await session.flush()
    card = (await get_context(["src/auth/service.py"]))["targets"]["src/auth/service.py"]

    assert card["fix_history"]["linked_issues"] == ["ENG-42 (bug): Checkout fails on an empty cart"]
    assert {k: v for k, v in card["fix_history"].items() if k != "linked_issues"} == plain[
        "fix_history"
    ]


async def _block_rows(tmp_path, links):
    ctx = await _ctx_with_events(tmp_path, [("src/app.py", [[10, 20]], 3)], shas=[_SHA])
    async with ctx.session_factory() as session:
        await store_issue_links(session, _REPO_ID, links, source="t")
        await session.commit()
    block = await _prior_fixes_block(ctx, {"src/app.py": {12}})
    return block["files"]


@pytest.mark.asyncio
async def test_prior_fixes_names_the_linked_bug_per_file(tmp_path):
    [row] = await _block_rows(tmp_path, [_LINK])
    assert row["linked_issues"] == ["ENG-42 (bug): Checkout fails on an empty cart"]


@pytest.mark.asyncio
async def test_prior_fixes_without_a_defect_link_keeps_its_row(tmp_path):
    feature = StoredLink(**{**_LINK.__dict__, "type": "feature"})
    [row] = await _block_rows(tmp_path / "f", [feature])
    [plain] = await _block_rows(tmp_path / "p", [])
    assert row == plain
    assert "linked_issues" not in row
