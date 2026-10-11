"""``get_context`` serves decisions in lanes, and a tombstone in none of them.

The block this pins used to select every ``DecisionRecord`` for the repository
with no status filter, no acceptance filter and no dismissed filter, and assign
the whole list to ``decisions``. A machine-mined candidate and a dismissed
tombstone both reached an agent as a rule the repository had settled on.

Both halves of the contract are tested here, because the easy half is the
tombstone. An accepted decision must still reach an agent exactly as it does
today, a candidate must reach it *distinguishably*, and the order must be the
one the Decisions page renders.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete

from repowise.core.persistence.models import DecisionAcceptance, DecisionRecord

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)
_TARGET = "src/auth/service.py"


def _record(rid: str, did: str, title: str, *, status: str, confidence: float = 0.8):
    return DecisionRecord(
        id=did,
        repository_id=rid,
        title=title,
        status=status,
        context="ctx",
        decision=f"{title} body",
        rationale=f"{title} rationale",
        alternatives_json=json.dumps([]),
        consequences_json=json.dumps([]),
        affected_files_json=json.dumps([_TARGET]),
        affected_modules_json=json.dumps([]),
        tags_json=json.dumps([]),
        source="session",
        confidence=confidence,
        staleness_score=0.0,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _acceptance(rid: str, did: str, currency: str) -> DecisionAcceptance:
    return DecisionAcceptance(
        repository_id=rid,
        decision_id=did,
        seq=1,
        action="accepted" if currency == "active" else currency,
        currency=currency,
        reason="a reason",
        scope_json=json.dumps([_TARGET]),
        evidence_json=json.dumps(["commit"]),
        accepter="tester",
    )


async def _lanes(session, rid, records) -> dict:
    """Seed *records* and return the target card ``get_context`` builds.

    The shared fixture ships two decisions of its own; this file controls its
    whole decision population so a lane assertion is about what it seeded.
    """
    await session.execute(delete(DecisionRecord))
    for r in records:
        session.add(r)
    await session.flush()
    from repowise.server.mcp_server import get_context

    result = await get_context([_TARGET], include=["decisions"], compact=True)
    return result["targets"][_TARGET]


@pytest.mark.asyncio
async def test_an_accepted_decision_still_reaches_the_agent(session, setup_mcp):
    """The half that is not the tombstone, and the one worth breaking."""
    rid = setup_mcp
    t = await _lanes(
        session,
        rid,
        [
            _record(rid, "d-acc", "Accepted rule", status="active"),
            _acceptance(rid, "d-acc", "active"),
        ],
    )
    titles = [d["title"] for d in t["decisions"]]
    assert "Accepted rule" in titles
    entry = next(d for d in t["decisions"] if d["title"] == "Accepted rule")
    # Every field the old block emitted is still emitted, unchanged.
    assert entry["id"] == "d-acc"
    assert entry["status"] == "active"
    assert entry["decision"] == "Accepted rule body"
    assert entry["rationale"] == "Accepted rule rationale"
    assert entry["confidence"] == 0.8
    # Plus the lane label, which is the new part.
    assert entry["authority"] == "accepted"
    assert entry["currency"] == "active"


@pytest.mark.asyncio
async def test_a_candidate_reaches_the_agent_in_its_own_lane(session, setup_mcp):
    rid = setup_mcp
    t = await _lanes(
        session, rid, [_record(rid, "d-cand", "Mined guess", status="proposed")]
    )
    assert [d["title"] for d in t["decisions"]] == []
    assert [d["title"] for d in t["candidates"]] == ["Mined guess"]
    assert t["candidates"][0]["authority"] == "candidate"


@pytest.mark.asyncio
async def test_a_status_column_alone_is_not_authority(session, setup_mcp):
    """``status = 'active'`` with no acceptance behind it is still a candidate.

    The column is a projection every writer keeps in step, so it agrees right
    up until something writes it without an acceptance — which is what
    ``unretire_auto_superseded`` and the resume path both do.
    """
    rid = setup_mcp
    t = await _lanes(
        session, rid, [_record(rid, "d-lying", "Active but unagreed", status="active")]
    )
    assert [d["title"] for d in t["decisions"]] == []
    assert [d["title"] for d in t["candidates"]] == ["Active but unagreed"]


@pytest.mark.asyncio
async def test_a_dismissed_candidate_reaches_nobody(session, setup_mcp):
    """A candidate dismissed without ever being accepted carries no acceptance
    row, so the acceptance test alone reads its tombstone as a live candidate.
    Only the SQL exclusion keeps it out — and it must keep out *this* record
    without also keeping out the accepted-then-withdrawn one below."""
    rid = setup_mcp
    t = await _lanes(
        session, rid, [_record(rid, "d-tomb", "Tombstoned", status="dismissed")]
    )
    assert "Tombstoned" not in [d["title"] for d in t["decisions"]]
    assert "Tombstoned" not in [d["title"] for d in t.get("candidates", [])]
    assert "Tombstoned" not in [d["title"] for d in t.get("history", [])]


@pytest.mark.asyncio
async def test_a_withdrawn_decision_is_history_not_a_rule(session, setup_mcp):
    rid = setup_mcp
    t = await _lanes(
        session,
        rid,
        [
            _record(rid, "d-sup", "Superseded rule", status="superseded"),
            _acceptance(rid, "d-sup", "superseded"),
        ],
    )
    assert "Superseded rule" not in [d["title"] for d in t["decisions"]]
    assert [d["title"] for d in t["history"]] == ["Superseded rule"]
    assert t["history"][0]["authority"] == "withdrawn"


@pytest.mark.asyncio
async def test_lanes_are_ordered_the_way_the_decisions_page_orders(session, setup_mcp):
    """Same records, same order, on both surfaces.

    ``decision_priority_order`` is what ``crud.list_decisions(sort="priority")``
    passes, and that is what the Decisions page renders. Within the candidate
    lane the order that reaches an agent has to be the order a person reading
    the dashboard sees, or the two disagree about which guess matters most.
    """
    rid = setup_mcp
    t = await _lanes(
        session,
        rid,
        [
            _record(rid, "d-low", "Low confidence", status="proposed", confidence=0.2),
            _record(rid, "d-high", "High confidence", status="proposed", confidence=0.9),
            _record(rid, "d-mid", "Mid confidence", status="proposed", confidence=0.5),
        ],
    )
    assert [d["title"] for d in t["candidates"]] == [
        "High confidence",
        "Mid confidence",
        "Low confidence",
    ]


@pytest.mark.asyncio
async def test_the_candidate_lane_is_capped(session, setup_mcp):
    """The response budget is one ceiling over the whole payload, so an
    uncapped lane does not appear beside the card — it displaces it."""
    from repowise.server.mcp_server.tool_context import targets as targets_mod

    rid = setup_mcp
    n = targets_mod._MAX_CANDIDATES + 3
    t = await _lanes(
        session,
        rid,
        [
            _record(rid, f"d-c{i}", f"Guess {i}", status="proposed", confidence=0.9)
            for i in range(n)
        ],
    )
    assert len(t["candidates"]) == targets_mod._MAX_CANDIDATES


@pytest.mark.asyncio
async def test_an_accepted_decision_that_names_nothing_is_not_a_rule(session, setup_mcp):
    """``uncheckable`` is accepted but not governing.

    ``is_governing`` is ``{"active", "needs_review"}``, so an accepted decision
    the repository can no longer check does not belong in the lane an agent
    reads as the rules. Tested beside ``superseded`` because the two reach the
    withdrawn branch by different routes.
    """
    rid = setup_mcp
    t = await _lanes(
        session,
        rid,
        [
            _record(rid, "d-unch", "Cannot be checked", status="active"),
            _acceptance(rid, "d-unch", "uncheckable"),
        ],
    )
    assert [d["title"] for d in t["decisions"]] == []
    assert [d["title"] for d in t["history"]] == ["Cannot be checked"]


@pytest.mark.asyncio
async def test_a_decision_accepted_then_dismissed_is_history(session, setup_mcp):
    """The other dismissal route, and it is not a tombstone.

    ``dismiss_candidate`` writes ``status = "dismissed"`` for both a candidate
    nobody ever agreed to and a decision somebody accepted and later withdrew.
    Only the acceptance row tells them apart. Filtering on the status alone
    drops the withdrawn decision from every lane, while the Decisions page's
    History lane still shows it — which is the divergence this phase exists to
    remove, pointing the other way.
    """
    rid = setup_mcp
    t = await _lanes(
        session,
        rid,
        [
            _record(rid, "d-wd", "Withdrawn outright", status="dismissed"),
            _acceptance(rid, "d-wd", "dismissed"),
        ],
    )
    assert [d["title"] for d in t["decisions"]] == []
    assert [d["title"] for d in t.get("candidates", [])] == []
    assert [d["title"] for d in t["history"]] == ["Withdrawn outright"]
    assert t["history"][0]["authority"] == "withdrawn"


@pytest.mark.asyncio
async def test_the_history_lane_is_capped(session, setup_mcp):
    from repowise.server.mcp_server.tool_context import targets as targets_mod

    rid = setup_mcp
    n = targets_mod._MAX_DECISION_HISTORY + 3
    records = []
    for i in range(n):
        records.append(_record(rid, f"d-h{i}", f"Retired {i}", status="superseded"))
        records.append(_acceptance(rid, f"d-h{i}", "superseded"))
    t = await _lanes(session, rid, records)
    assert len(t["history"]) == targets_mod._MAX_DECISION_HISTORY


@pytest.mark.asyncio
async def test_empty_lanes_cost_nothing(session, setup_mcp):
    """``decisions`` is always present; the two added lanes appear only when
    they hold something, so the common card does not grow two empty lists."""
    rid = setup_mcp
    t = await _lanes(
        session,
        rid,
        [
            _record(rid, "d-only", "Accepted only", status="active"),
            _acceptance(rid, "d-only", "active"),
        ],
    )
    assert "decisions" in t
    assert "history" not in t
