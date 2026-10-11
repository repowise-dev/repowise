"""``get_overview``'s ``next_actions``: the stored actions, compacted, never fatal."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repowise.server.mcp_server.tool_overview.actions import compact_actions_view


async def _seed(session, rid: str) -> None:
    from repowise.core.persistence.models import DeadCodeFinding, GitCommit, SecurityFinding

    session.add(
        GitCommit(repository_id=rid, sha="head", author_name="Ada", author_email="ada@x.io",
                  committed_at=datetime(2026, 9, 28, 12, tzinfo=UTC), subject="chore: head")
    )  # fmt: skip
    session.add_all(
        DeadCodeFinding(repository_id=rid, kind="unused_export", file_path="src/dead.py",
                        symbol_name=f"f{i}", lines=10, safe_to_delete=True)
        for i in range(3)
    )  # fmt: skip
    session.add(
        SecurityFinding(repository_id=rid, file_path="src/settings.py", kind="hardcoded_secret",
                        severity="high", snippet='TOKEN = "sk_live_123"', line_number=4,
                        commit_sha="")
    )  # fmt: skip
    await session.commit()


@pytest.mark.asyncio
async def test_overview_carries_the_stored_actions(setup_mcp, session):
    from repowise.core.persistence.crud.analysis.actions import load_actions_view
    from repowise.server.mcp_server import get_overview

    await _seed(session, setup_mcp)
    result = await get_overview()

    block = result["next_actions"]
    assert "next_actions_reason" not in result
    view = await load_actions_view(session, setup_mcp)
    for horizon in ("week", "quarter"):
        full = view["horizons"][horizon]
        assert block[horizon]["total"] == full["total"]
        assert block[horizon]["by_tier"] == full["by_tier"]
        # Same rows, same order, as the UI reads.
        assert [a["id"] for a in block[horizon]["actions"]] == [
            a["id"] for a in full["actions"][:3]
        ]
    assert block["quarter"]["actions"], "the seed should produce actions"
    for row in block["quarter"]["actions"]:
        assert set(row) == {"id", "tier", "title", "impact", "done_when", "target"}
        assert None not in row["target"].values()


@pytest.mark.asyncio
async def test_a_failed_read_is_omitted_with_a_reason(setup_mcp, monkeypatch):
    from repowise.server.mcp_server import get_overview
    from repowise.server.mcp_server.tool_overview import actions as mod

    async def boom(session, repo_id):
        raise RuntimeError("no such table: action_states")

    monkeypatch.setattr(mod, "load_actions_view", boom)
    result = await get_overview()

    assert "next_actions" not in result
    assert "RuntimeError" in result["next_actions_reason"]
    # The rest of the overview is untouched.
    assert result["title"] == "Test Repo Overview"
    assert result["key_modules"]


def test_compact_view_keeps_three_rows_and_names_unavailable_stores():
    rows = [
        {"id": f"act_{i}", "tier": "plan", "title": f"T{i}", "impact": "i", "done_when": "d",
         "target": {"kind": "file", "path": f"a{i}.py", "symbol": None}, "why": [], "rule": "x"}
        for i in range(5)
    ]  # fmt: skip
    view = {
        "anchor": "2026-09-28T12:00:00",
        "horizons": {
            "week": {"actions": rows, "total": 9, "hidden": 0, "by_tier": {"plan": 9}},
            "quarter": {"actions": [], "total": 0, "hidden": 0, "by_tier": {}},
        },
        "summary": {"week": "9 things worth doing this week.", "quarter": "Nothing."},
        "unavailable": {"security": "why", "performance": "why"},
    }

    block = compact_actions_view(view)

    # The agent reads counts; the sentence stays on the human surfaces.
    assert "summary" not in block

    assert [a["id"] for a in block["week"]["actions"]] == ["act_0", "act_1", "act_2"]
    assert block["week"]["total"] == 9
    assert block["week"]["actions"][0]["target"] == {"kind": "file", "path": "a0.py"}
    assert block["quarter"] == {"total": 0, "by_tier": {}, "actions": []}
    assert block["unavailable"] == ["performance", "security"]


def test_budget_sheds_the_block_before_the_protected_ones():
    from repowise.server.mcp_server._budget.contracts import _CONTRACTS

    contract = _CONTRACTS["get_overview"]
    order = contract.shed_order
    assert order.index("next_actions.quarter.actions[]") < order.index("next_actions")
    assert "next_actions" not in contract.protected



def _factory(url: str):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from repowise.core.persistence import create_engine

    return async_sessionmaker(create_engine(url))


def _record(monkeypatch, gate=None):
    """Stand-ins for the two slow blocks that note the session each read on."""
    from repowise.server.mcp_server.tool_overview import tool

    seen: dict = {}

    async def code_health(session, _repo):
        seen["code_health"] = session
        if gate is not None:
            await gate.wait()  # finishes only if next_actions runs meanwhile
        return {"average_health": 7.0}

    async def next_actions(session, _repo):
        seen["next_actions"] = session
        if gate is not None:
            gate.set()
        return {}, None

    monkeypatch.setattr(tool, "_build_code_health", code_health)
    monkeypatch.setattr(tool, "_build_next_actions", next_actions)
    return seen


@pytest.mark.asyncio
async def test_on_a_file_store_the_slow_blocks_read_together_on_their_own_sessions(
    tmp_path, monkeypatch
):
    import asyncio

    from repowise.server.mcp_server.tool_overview.tool import _slow_reads

    seen = _record(monkeypatch, gate=asyncio.Event())
    caller = object()
    slow = _slow_reads(caller, _factory(f"sqlite+aiosqlite:///{(tmp_path / 'w.db').as_posix()}"), None)
    health, actions = await asyncio.wait_for(slow, timeout=10)

    assert health == {"average_health": 7.0} and actions == ({}, None)
    assert seen["code_health"] is not caller and seen["next_actions"] is not caller
    assert seen["code_health"] is not seen["next_actions"]


@pytest.mark.asyncio
async def test_on_a_shared_connection_they_read_in_turn_on_the_callers_session(monkeypatch):
    from repowise.server.mcp_server.tool_overview.tool import _slow_reads

    seen = _record(monkeypatch)
    caller = object()
    slow = _slow_reads(caller, _factory("sqlite+aiosqlite:///:memory:"), None)
    assert seen == {}, "nothing reads before the caller's own reads are done"
    await slow
    assert seen == {"code_health": caller, "next_actions": caller}


@pytest.mark.asyncio
async def test_a_failed_overview_leaves_no_slow_read_running(tmp_path, monkeypatch):
    import asyncio

    from repowise.server.mcp_server.tool_overview.tool import _abandon, _slow_reads

    _record(monkeypatch, gate=asyncio.Event())
    from repowise.server.mcp_server.tool_overview import tool

    async def stuck(session, _repo):
        await asyncio.Event().wait()

    monkeypatch.setattr(tool, "_build_next_actions", stuck)
    before = asyncio.all_tasks()
    slow = _slow_reads(object(), _factory(f"sqlite+aiosqlite:///{(tmp_path / 'w.db').as_posix()}"), None)
    await asyncio.sleep(0)
    await _abandon(slow)

    assert slow.done()
    assert asyncio.all_tasks() <= before
    # The fallback is a coroutine that never started; abandoning closes it.
    lazy = _slow_reads(object(), _factory("sqlite+aiosqlite:///:memory:"), None)
    await _abandon(lazy)
    assert lazy.cr_frame is None
