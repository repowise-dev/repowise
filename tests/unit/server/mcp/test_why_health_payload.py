"""What the health dashboard spends an agent's context on.

This mode is an orientation call: asked once, skimmed. It served 45 items
to be read as a verdict. The cut is only defensible now that
``get_decision_health_summary`` ranks what it returns, so these pin both halves:
the tool takes the front of each list, and it does not re-order it on the way
out. A tool that re-sorted here would put the owner's ranking back at the call
site, which is the defect this whole change removes.
"""

from __future__ import annotations

import json

import pytest

from repowise.core.persistence.models import DecisionRecord
from repowise.server.mcp_server.tool_why import (
    _MAX_HEALTH_PROPOSED,
    _MAX_HEALTH_STALE,
    _MAX_HEALTH_UNGOVERNED,
)


def _record(rec_id: str, *, staleness: float = 0.0, confidence: float = 1.0) -> DecisionRecord:
    return DecisionRecord(
        id=rec_id,
        repository_id="repo-1",
        title=f"Decision {rec_id}",
        decision="body",
        status="active",
        source="cli",
        staleness_score=staleness,
        confidence=confidence,
        affected_files_json=json.dumps([f"src/{rec_id}.py"]),
        affected_modules_json="[]",
    )


@pytest.fixture
def oversized_health(monkeypatch):
    """A summary far longer than every cap, already ranked as the owner ranks it."""
    from repowise.core.persistence import crud

    stale = [_record(f"s{i}", staleness=1.0 - i / 100) for i in range(20)]
    proposed = [_record(f"p{i}", confidence=1.0 - i / 100) for i in range(20)]
    ungoverned = [f"src/hot_{i}.py" for i in range(20)]
    retired = [("superseded", _record(f"r{i}")) for i in range(20)]
    unscoped = [_record(f"u{i}", confidence=1.0 - i / 100) for i in range(20)]

    async def _fake(session, repository_id):
        return {
            "summary": {"active": 20, "proposed": 20, "stale": 20, "conflicts": 0},
            "stale_decisions": stale,
            "proposed_awaiting_review": proposed,
            "ungoverned_hotspots": ungoverned,
            "conflicts": [],
            "retired_decisions": retired,
            "unscoped_decisions": unscoped,
        }

    monkeypatch.setattr(crud, "get_decision_health_summary", _fake)
    return stale, proposed, ungoverned, retired, unscoped


@pytest.mark.asyncio
async def test_health_serves_five_five_and_eight(setup_mcp, oversized_health):
    """The sizes are written out, not read back from the constants they pin.

    Asserting against ``_MAX_HEALTH_STALE`` would hold for every value of it,
    including one that cuts nothing. These three numbers are a judgement
    about what an orientation call should cost an agent, so changing one should
    have to change a test.
    """
    from repowise.server.mcp_server import get_why

    result = await get_why()

    assert result["mode"] == "health"
    assert len(result["stale_decisions"]) == 5
    assert len(result["proposed_awaiting_review"]) == 5
    assert len(result["ungoverned_hotspots"]) == 8


@pytest.mark.asyncio
async def test_health_takes_the_front_of_each_list_without_re_sorting_it(
    setup_mcp, oversized_health
):
    """The owner ranks; this mode may truncate and must not re-order.

    Re-sorting here would put the ranking back at the call site, which is the
    defect the change removes.
    """
    from repowise.server.mcp_server import get_why

    stale, proposed, ungoverned, _retired, _unscoped = oversized_health
    result = await get_why()

    assert [d["id"] for d in result["stale_decisions"]] == [
        d.id for d in stale[:_MAX_HEALTH_STALE]
    ]
    assert [d["id"] for d in result["proposed_awaiting_review"]] == [
        d.id for d in proposed[:_MAX_HEALTH_PROPOSED]
    ]
    assert result["ungoverned_hotspots"] == ungoverned[:_MAX_HEALTH_UNGOVERNED]


@pytest.mark.asyncio
async def test_health_still_states_the_sizes_it_is_not_showing(setup_mcp, oversized_health):
    """Cutting is only honest while the full counts stay legible."""
    from repowise.server.mcp_server import get_why

    result = await get_why()

    assert result["counts"]["stale"] == 20
    assert result["counts"]["proposed"] == 20
    # The summary line is where the ungoverned total survives. It has no
    # counter of its own, so cutting the list without this would hide it.
    assert "20 ungoverned hotspots" in result["summary"]


@pytest.mark.asyncio
async def test_health_names_the_lanes_it_used_to_only_count(setup_mcp, oversized_health):
    """``counts.superseded: 3`` with no way to learn which three was the defect.

    These two lanes had no list at all, so unlike every capped lane here there
    was nothing to recover: no filter argument reaches them, no id was emitted
    to feed ``get_why(id=...)``, and the omission store can only return rows
    that entered the response.
    """
    from repowise.server.mcp_server import get_why

    _stale, _proposed, _ungoverned, retired, unscoped = oversized_health
    result = await get_why()

    assert [row["id"] for row in result["retired_decisions"]] == [
        record.id for _lane, record in retired[:5]
    ]
    assert result["retired_decisions"][0]["lane"] == "superseded"
    assert [row["id"] for row in result["unscoped_decisions"]] == [
        record.id for record in unscoped[:5]
    ]


@pytest.mark.asyncio
async def test_health_states_the_sizes_of_the_new_lanes_too(setup_mcp, oversized_health):
    """A cap is only honest while what it cut is still reachable."""
    from repowise.server.mcp_server import get_why

    result = await get_why()

    assert result["retired_decisions_total"] == 20
    assert result["retired_decisions_emitted"] == 5
    assert result["unscoped_decisions_total"] == 20
    assert result["unscoped_decisions_emitted"] == 5


@pytest.mark.asyncio
async def test_health_leaves_the_conflicts_lane_alone(setup_mcp, oversized_health):
    """``conflicts`` already names both sides with id, title and status.

    It was named alongside these lanes and is not one of them: it is richer
    than ``proposed_awaiting_review``, which carries no status field at all.
    """
    from repowise.server.mcp_server import get_why

    result = await get_why()

    assert result["conflicts"] == []
