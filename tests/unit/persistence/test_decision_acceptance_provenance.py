"""What signed an acceptance, not only who.

``accepter`` resolves to ``git config user.name``, so before these columns an
agent accepting through the CLI signed the maintainer's name and no reader
could tell the two apart. The contract under test is that every new row says
what signed it, and that a machine may withdraw authority but not grant it
unless the repository turned that on.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from repowise.core.analysis.decisions.lifecycle import (
    ACCEPTER_KINDS,
    ACCEPTER_SESSION_MAX,
    GRANTING_ACTIONS,
    accepter_kind_blocker,
    machine_grant_blocker,
)
from repowise.core.persistence.crud import bulk_upsert_decisions
from repowise.core.persistence.crud.authority import (
    AcceptanceRefusedError,
    accept_decision,
    decision_signatures,
    dismiss_candidate,
    latest_acceptance,
    reaffirm_decision,
    record_acceptance,
)
from repowise.core.persistence.models import DecisionAcceptance, DecisionRecord
from tests.unit.persistence.helpers import insert_repo


async def _seed(session, repo_id: str, title: str) -> DecisionRecord:
    await bulk_upsert_decisions(
        session,
        repo_id,
        [
            {
                "title": title,
                "decision": f"{title}: do the thing",
                "rationale": "because the alternative was measured slower",
                "source": "session",
                "status": "proposed",
                "affected_files": ["src/app.py"],
                "evidence_file": "src/app.py",
                "confidence": 0.7,
                "verification": "exact",
                "source_quote": f"{title}: do the thing",
            }
        ],
    )
    return (
        await session.execute(
            select(DecisionRecord).where(
                DecisionRecord.repository_id == repo_id, DecisionRecord.title == title
            )
        )
    ).scalar_one()


# ---------------------------------------------------------------------------
# The kind is required, and '' is not one
# ---------------------------------------------------------------------------


async def test_an_acceptance_records_what_signed_it(async_session):
    repo = await insert_repo(async_session)
    rec = await _seed(async_session, repo.id, "Use Redis")

    await accept_decision(async_session, rec, accepter="Raghav", kind="person")

    row = await latest_acceptance(async_session, rec.id)
    assert row is not None
    assert (row.accepter, row.accepter_kind) == ("Raghav", "person")


async def test_the_accepting_session_is_stored_beside_the_kind(async_session):
    """What turns "an agent" into a transcript somebody can go and read."""
    repo = await insert_repo(async_session)
    rec = await _seed(async_session, repo.id, "Use Redis")

    await accept_decision(
        async_session,
        rec,
        accepter="claude_code",
        kind="agent",
        accepter_session="sess-42",
        agent_acceptance=True,
    )

    row = await latest_acceptance(async_session, rec.id)
    assert (row.accepter_kind, row.accepter_session) == ("agent", "sess-42")


async def test_the_only_writer_refuses_an_unstated_kind(async_session):
    """'' is the pre-provenance vintage, never a value a new row may claim.

    Without this refusal the column defaults to '' and every surface has to
    decide for itself whether that means "a person" — which is the ambiguity
    the column exists to remove.
    """
    repo = await insert_repo(async_session)
    rec = await _seed(async_session, repo.id, "Use Redis")

    with pytest.raises(AcceptanceRefusedError, match="accepter kind"):
        await record_acceptance(
            async_session, rec, action="accepted", currency="active", accepter="x", kind=""
        )
    assert await latest_acceptance(async_session, rec.id) is None


async def test_an_unknown_kind_is_refused_by_name(async_session):
    repo = await insert_repo(async_session)
    rec = await _seed(async_session, repo.id, "Use Redis")

    with pytest.raises(AcceptanceRefusedError, match="robot"):
        await record_acceptance(
            async_session, rec, action="accepted", currency="active", accepter="x", kind="robot"
        )


# ---------------------------------------------------------------------------
# Machines revoke; they do not grant
# ---------------------------------------------------------------------------


async def test_an_agent_cannot_accept_by_default(async_session):
    repo = await insert_repo(async_session)
    rec = await _seed(async_session, repo.id, "Use Redis")

    with pytest.raises(AcceptanceRefusedError, match="may not record a 'accepted'"):
        await accept_decision(async_session, rec, accepter="claude_code", kind="agent")
    assert await latest_acceptance(async_session, rec.id) is None


async def test_an_agent_cannot_reaffirm_by_default(async_session):
    """Reaffirming renews authority, so it is a grant and not a read."""
    repo = await insert_repo(async_session)
    rec = await _seed(async_session, repo.id, "Use Redis")
    await accept_decision(async_session, rec, accepter="Raghav", kind="person")

    with pytest.raises(AcceptanceRefusedError, match="may not record a 'reaffirmed'"):
        await reaffirm_decision(async_session, rec, accepter="claude_code", kind="agent")


async def test_an_agent_may_withdraw_authority_without_the_switch(async_session):
    """The asymmetry, stated: a wrong withdrawal is recoverable, a wrong grant is not."""
    repo = await insert_repo(async_session)
    rec = await _seed(async_session, repo.id, "Use Redis")
    await accept_decision(async_session, rec, accepter="Raghav", kind="person")

    await dismiss_candidate(async_session, rec, reason="reversed", accepter="evolution", kind="agent")

    row = await latest_acceptance(async_session, rec.id)
    assert (row.action, row.accepter_kind) == ("dismissed", "agent")


async def test_the_policy_switch_is_what_lets_an_agent_accept(async_session):
    repo = await insert_repo(async_session)
    rec = await _seed(async_session, repo.id, "Use Redis")

    await accept_decision(
        async_session, rec, accepter="claude_code", kind="agent", agent_acceptance=True
    )

    row = await latest_acceptance(async_session, rec.id)
    assert (row.action, row.accepter_kind) == ("accepted", "agent")


async def test_the_switch_does_not_loosen_a_person_or_an_import(async_session):
    """It grants exactly one thing, so turning it on cannot widen anything else."""
    for kind in ACCEPTER_KINDS:
        for action in GRANTING_ACTIONS:
            granted = machine_grant_blocker(kind, action, granted=True)
            ungranted = machine_grant_blocker(kind, action, granted=False)
            assert granted is None
            if kind != "agent":
                assert ungranted is None


async def test_an_oversized_session_is_refused_not_truncated(async_session):
    """SQLite ignores the width and Postgres raises; both are worse than a refusal."""
    repo = await insert_repo(async_session)
    rec = await _seed(async_session, repo.id, "Use Redis")

    with pytest.raises(AcceptanceRefusedError, match="accepter session is longer"):
        await accept_decision(
            async_session,
            rec,
            accepter="claude_code",
            kind="agent",
            accepter_session="x" * (ACCEPTER_SESSION_MAX + 1),
            agent_acceptance=True,
        )
    assert await latest_acceptance(async_session, rec.id) is None


def test_the_session_bound_matches_the_column_it_protects():
    from repowise.core.persistence.models import DecisionAcceptance

    width = DecisionAcceptance.__table__.c.accepter_session.type.length
    assert width == ACCEPTER_SESSION_MAX


def test_every_named_kind_is_storable_and_the_empty_one_is_not():
    assert [k for k in ACCEPTER_KINDS if accepter_kind_blocker(k)] == []
    assert accepter_kind_blocker("") is not None


# ---------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------


async def test_a_candidate_has_no_signature(async_session):
    """Absent, not blank: a surface must not render an unsigned row as signed."""
    repo = await insert_repo(async_session)
    rec = await _seed(async_session, repo.id, "Use Redis")

    assert await decision_signatures(async_session, repo.id, [rec]) == {}


async def test_the_signature_read_follows_the_latest_row(async_session):
    """A withdrawal by a machine must not keep showing the person who accepted."""
    repo = await insert_repo(async_session)
    rec = await _seed(async_session, repo.id, "Use Redis")
    await accept_decision(async_session, rec, accepter="Raghav", kind="person")
    assert (await decision_signatures(async_session, repo.id, [rec]))[rec.id].kind == "person"

    await dismiss_candidate(
        async_session, rec, reason="reversed", accepter="evolution", kind="agent"
    )
    signature = (await decision_signatures(async_session, repo.id, [rec]))[rec.id]
    assert (signature.kind, signature.accepter) == ("agent", "evolution")


async def test_seq_and_not_arrival_order_decides_who_signed(async_session):
    """The log is append-only, so the current row is the highest ``seq``.

    Arrival order agrees with ``seq`` in the ordinary case, which is why a read
    that ignores ``seq`` looks correct: it is the concurrent append — the one
    the ``seq`` retry exists for — that separates them. Written with a lower
    ``seq`` landing last so a read that takes whatever the scan returned picks
    the superseded row.
    """
    repo = await insert_repo(async_session)
    rec = await _seed(async_session, repo.id, "Use Redis")
    await accept_decision(async_session, rec, accepter="Raghav", kind="person")
    await dismiss_candidate(
        async_session, rec, reason="reversed", accepter="evolution", kind="agent"
    )

    # seq 1 again, inserted after seq 2: only the seq restriction rejects it.
    stale = (
        await async_session.execute(
            select(DecisionAcceptance).where(
                DecisionAcceptance.decision_id == rec.id, DecisionAcceptance.seq == 1
            )
        )
    ).scalar_one()
    async_session.expunge(stale)
    await async_session.execute(
        delete(DecisionAcceptance).where(
            DecisionAcceptance.decision_id == rec.id, DecisionAcceptance.seq == 1
        )
    )
    await async_session.flush()
    async_session.add(
        DecisionAcceptance(
            repository_id=repo.id,
            decision_id=rec.id,
            seq=1,
            action="accepted",
            currency="active",
            reason=stale.reason,
            scope_json=stale.scope_json,
            evidence_json=stale.evidence_json,
            accepter="Raghav",
            accepter_kind="person",
        )
    )
    await async_session.flush()

    signature = (await decision_signatures(async_session, repo.id, [rec]))[rec.id]
    assert (signature.kind, signature.accepter) == ("agent", "evolution")
