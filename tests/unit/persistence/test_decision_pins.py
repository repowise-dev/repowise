"""Pinning: a mark in the acceptance log that a person holds a decision as law.

A pin is a log row like any other, so the history keeps it, but it never
becomes the decision's current authority: who accepted it and what it amounts
to read the same before and after. A pinned decision is withdrawn only by a
person who says so; an agent is refused and told to ask.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from repowise.core.persistence.crud.authority import (
    AcceptanceRefusedError,
    accept_decision,
    current_currency,
    decision_signatures,
    deprecate_decision,
    dismiss_candidate,
    is_pinned,
    latest_acceptance,
    pinned_decision_ids,
    record_acceptance,
    set_pinned,
    supersede_decision,
)
from repowise.core.persistence.crud.decision_digest import recent_decisions
from repowise.core.persistence.models import DecisionAcceptance, DecisionRecord, _now_utc
from tests.unit.persistence.helpers import insert_repo


async def _record(session, repo_id, rec_id, **overrides) -> DecisionRecord:
    fields = {
        "title": f"title {rec_id}",
        "decision": f"decision {rec_id}",
        "rationale": "why",
        "status": "proposed",
        "source": "pr",
        "affected_files_json": json.dumps(["src/app.py"]),
        **overrides,
    }
    rec = DecisionRecord(id=rec_id, repository_id=repo_id, **fields)
    session.add(rec)
    await session.flush()
    return rec


async def _in_force(session, repo_id, rec_id, *, kind="agent", **overrides) -> DecisionRecord:
    rec = await _record(session, repo_id, rec_id, **overrides)
    await accept_decision(
        session, rec, accepter="claude_code", kind=kind, agent_acceptance=True,
        evidence=["abc1234"],
    )
    return rec


async def _actions(session, rec_id) -> list[str]:
    return list(
        await session.scalars(
            select(DecisionAcceptance.action)
            .where(DecisionAcceptance.decision_id == rec_id)
            .order_by(DecisionAcceptance.seq)
        )
    )


async def test_a_pin_is_logged_and_changes_neither_authority_nor_signer(async_session):
    repo = await insert_repo(async_session)
    rec = await _in_force(async_session, repo.id, "d1")
    before = await latest_acceptance(async_session, rec.id)

    await set_pinned(async_session, rec, True, accepter="Raghav", kind="person", reason="law")

    assert await _actions(async_session, rec.id) == ["accepted", "pinned"]
    pin = (
        await async_session.scalars(
            select(DecisionAcceptance).where(DecisionAcceptance.action == "pinned")
        )
    ).one()
    # Restates the current row so the log's contract holds, signed by the pinner.
    assert (pin.currency, pin.accepter, pin.accepter_kind) == ("active", "Raghav", "person")
    assert json.loads(pin.scope_json) == json.loads(before.scope_json)
    assert json.loads(pin.evidence_json) == ["abc1234"]
    # The current authority is still the agent's acceptance.
    assert (await latest_acceptance(async_session, rec.id)).id == before.id
    signed = (await decision_signatures(async_session, repo.id, [rec]))[rec.id]
    assert (signed.accepter, signed.kind) == ("claude_code", "agent")
    assert await current_currency(async_session, rec) == "active"
    assert rec.status == "active"
    assert await is_pinned(async_session, rec.id)
    assert await pinned_decision_ids(async_session, repo.id) == {"d1"}


async def test_unpin_ends_it_and_a_later_reaffirm_keeps_it(async_session):
    repo = await insert_repo(async_session)
    kept = await _in_force(async_session, repo.id, "d1")
    ended = await _in_force(async_session, repo.id, "d2")
    for rec in (kept, ended):
        await set_pinned(async_session, rec, True, accepter="Raghav", kind="person")
    await record_acceptance(
        async_session, kept, action="reaffirmed", currency="active", kind="person",
        accepter="Raghav",
    )
    await set_pinned(async_session, ended, False, accepter="claude_code", kind="agent")

    assert await _actions(async_session, ended.id) == ["accepted", "pinned", "unpinned"]
    assert await pinned_decision_ids(async_session, repo.id) == {"d1"}
    assert await current_currency(async_session, ended) == "active"


@pytest.mark.parametrize(
    ("pinned", "message"),
    [(True, "already pinned"), (False, "not pinned")],
)
async def test_a_pin_that_would_change_nothing_is_refused(async_session, pinned, message):
    repo = await insert_repo(async_session)
    rec = await _in_force(async_session, repo.id, "d1")
    if not pinned:
        # Not pinned yet: unpinning is the no-op.
        with pytest.raises(AcceptanceRefusedError, match=message):
            await set_pinned(async_session, rec, False, accepter="p", kind="person")
        return
    await set_pinned(async_session, rec, True, accepter="p", kind="person")
    with pytest.raises(AcceptanceRefusedError, match=message):
        await set_pinned(async_session, rec, True, accepter="p", kind="person")


async def test_only_a_decision_in_force_can_be_pinned(async_session):
    repo = await insert_repo(async_session)
    background = await _record(async_session, repo.id, "bg")
    retired = await _in_force(async_session, repo.id, "old")
    await deprecate_decision(async_session, retired, accepter="p", kind="person")

    with pytest.raises(AcceptanceRefusedError, match="background, not in force"):
        await set_pinned(async_session, background, True, accepter="p", kind="person")
    with pytest.raises(AcceptanceRefusedError, match="retired"):
        await set_pinned(async_session, retired, True, accepter="p", kind="person")
    assert await _actions(async_session, background.id) == []


async def test_record_acceptance_never_writes_a_mark(async_session):
    repo = await insert_repo(async_session)
    rec = await _in_force(async_session, repo.id, "d1")
    with pytest.raises(ValueError, match="set_pinned"):
        await record_acceptance(
            async_session, rec, action="pinned", currency="active", kind="person", accepter="p"
        )


async def test_an_agent_cannot_replace_a_pinned_decision_and_is_told_to_ask(async_session):
    repo = await insert_repo(async_session)
    rec = await _in_force(async_session, repo.id, "d1")
    successor = await _in_force(async_session, repo.id, "d2")
    await set_pinned(async_session, rec, True, accepter="Raghav", kind="person")

    with pytest.raises(AcceptanceRefusedError) as refused:
        await supersede_decision(
            async_session, rec, successor_id=successor.id, accepter="claude_code", kind="agent",
            force=True,  # an agent's force is no person's say-so
        )
    assert "a person marked this decision as law. Ask them before replacing it" in str(
        refused.value
    )
    assert "`repowise decision unpin d1`" in str(refused.value)
    for withdraw in (deprecate_decision, dismiss_candidate):
        with pytest.raises(AcceptanceRefusedError, match="Ask them"):
            await withdraw(async_session, rec, accepter="claude_code", kind="agent")
    assert await current_currency(async_session, rec) == "active"
    assert rec.superseded_by is None


async def test_a_person_replaces_a_pinned_decision_only_with_force(async_session):
    repo = await insert_repo(async_session)
    rec = await _in_force(async_session, repo.id, "d1")
    successor = await _in_force(async_session, repo.id, "d2")
    await set_pinned(async_session, rec, True, accepter="Raghav", kind="person")

    with pytest.raises(AcceptanceRefusedError, match="replace it anyway with --force"):
        await supersede_decision(
            async_session, rec, successor_id=successor.id, accepter="Raghav", kind="person"
        )
    await supersede_decision(
        async_session, rec, successor_id=successor.id, accepter="Raghav", kind="person",
        force=True,
    )

    assert await current_currency(async_session, rec) == "superseded"
    # Leaving force ends the pin.
    assert not await is_pinned(async_session, rec.id)
    assert await pinned_decision_ids(async_session, repo.id) == set()


async def test_an_unpinned_decision_is_replaced_as_before(async_session):
    repo = await insert_repo(async_session)
    rec = await _in_force(async_session, repo.id, "d1")
    successor = await _in_force(async_session, repo.id, "d2")
    await supersede_decision(
        async_session, rec, successor_id=successor.id, accepter="claude_code", kind="agent"
    )
    assert await current_currency(async_session, rec) == "superseded"


async def test_the_revert_pass_leaves_a_pinned_decision_alone(async_session):
    from repowise.core.analysis.decisions.evolution import _retire

    repo = await insert_repo(async_session)
    rec = await _in_force(async_session, repo.id, "d1")
    await set_pinned(async_session, rec, True, accepter="Raghav", kind="person")

    assert await _retire(async_session, rec) is False
    assert rec.status == "active"
    assert await current_currency(async_session, rec) == "active"


# ---------------------------------------------------------------------------
# Recent: what came into force this week
# ---------------------------------------------------------------------------


async def _age(session, rec_id: str, days: int) -> None:
    for row in await session.scalars(
        select(DecisionAcceptance).where(DecisionAcceptance.decision_id == rec_id)
    ):
        row.created_at = _now_utc() - timedelta(days=days)
    await session.flush()


async def test_recent_lists_this_weeks_decisions_newest_first_with_who_and_where(async_session):
    repo = await insert_repo(async_session)
    older = await _in_force(async_session, repo.id, "older", kind="person")
    await _age(async_session, older.id, 3)
    landed = await _record(async_session, repo.id, "landed", source="agent")
    await accept_decision(
        async_session, landed, accepter="claude_code", artifact="def5678abc",
        kind="import", accepter_session="s-1", evidence=["def5678abc"],
        note="landed on main in def5678ab",
    )
    await set_pinned(async_session, older, True, accepter="Raghav", kind="person")

    rows, count = await recent_decisions(async_session, repo.id)

    assert count == 2
    assert [r.id for r in rows] == ["landed", "older"]
    first, second = rows
    # The merge put it in force, signed as the agent that recorded it.
    assert (first.signer, first.signer_kind, first.signer_session) == ("claude_code", "agent", "s-1")
    assert (first.commit, first.landed_on) == ("def5678abc", "main")
    # Stamped as UTC, so a browser does not read it as local time.
    assert first.at.endswith("+00:00")
    assert (second.signer_kind, second.commit, second.landed_on) == ("person", "abc1234", "")
    # The pin does not make it new again; it is still dated by its acceptance.
    assert second.pinned and not first.pinned


async def test_recent_leaves_out_old_replayed_and_retired_decisions(async_session):
    repo = await insert_repo(async_session)
    old = await _in_force(async_session, repo.id, "old")
    await _age(async_session, old.id, 8)
    migrated = await _record(async_session, repo.id, "migrated")
    await accept_decision(
        async_session, migrated, accepter="migration:pr", kind="import", evidence=["x"],
        note="reconstructed from a self-authored legacy record",
    )
    retired = await _in_force(async_session, repo.id, "retired")
    await deprecate_decision(async_session, retired, accepter="p", kind="person")
    kept = await _in_force(async_session, repo.id, "kept")

    rows, count = await recent_decisions(async_session, repo.id, limit=5)
    assert [r.id for r in rows] == [kept.id]
    assert count == 1
    assert (await recent_decisions(async_session, repo.id, days=10))[1] == 2
