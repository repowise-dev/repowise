"""Acceptance is the only thing that makes a record govern.

The contract under test is the entity split itself: a ``DecisionRecord`` with no
row in ``decision_acceptances`` is a candidate, no matter what its status column
says, and nothing but an explicit action or a tracked artifact writes one.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.crud import bulk_upsert_decisions, update_decision_status
from repowise.core.persistence.crud.authority import (
    AcceptanceRefusedError,
    accept_decision,
    accepted_decision_ids,
    accepted_predicate,
    current_currency,
    dismiss_candidate,
    is_accepted,
    latest_acceptance,
    list_candidates,
    merge_candidate,
    reaffirm_decision,
    request_split,
    resolve_decision_id,
    return_to_review,
    supersede_decision,
)
from repowise.core.persistence.decision_graph import get_decision_edges
from repowise.core.persistence.models import DecisionAcceptance, DecisionRecord
from tests.unit.persistence.helpers import insert_repo


def _dict(title: str, **overrides) -> dict:
    base = {
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
    base.update(overrides)
    return base


async def _seed(session, repo_id: str, *titles: str) -> list[DecisionRecord]:
    await bulk_upsert_decisions(session, repo_id, [_dict(t) for t in titles])
    rows = (
        (
            await session.execute(
                select(DecisionRecord).where(DecisionRecord.repository_id == repo_id)
            )
        )
        .scalars()
        .all()
    )
    by_title = {r.title: r for r in rows}
    return [by_title[t] for t in titles]


# ---------------------------------------------------------------------------
# The separation itself
# ---------------------------------------------------------------------------


async def test_extraction_produces_candidates_not_decisions(async_session):
    repo = await insert_repo(async_session)
    (rec,) = await _seed(async_session, repo.id, "Use Redis")

    assert not await is_accepted(async_session, rec.id)
    assert await accepted_decision_ids(async_session, repo.id) == set()
    assert await current_currency(async_session, rec) is None


async def test_a_governance_read_cannot_reach_a_candidate(async_session):
    """The predicate every governing surface adds is a join, not a status test."""
    repo = await insert_repo(async_session)
    candidate, decision = await _seed(async_session, repo.id, "Candidate", "Decision")
    await accept_decision(async_session, decision, accepter="tester")

    # A candidate forced to look accepted by the legacy column is still a
    # candidate, because the column is a projection and not the authority.
    candidate.status = "active"
    await async_session.flush()

    governing = (
        (
            await async_session.execute(
                select(DecisionRecord).where(
                    DecisionRecord.repository_id == repo.id, accepted_predicate()
                )
            )
        )
        .scalars()
        .all()
    )
    assert [r.id for r in governing] == [decision.id]


async def test_acceptance_requires_reason_scope_evidence_and_identity(async_session):
    repo = await insert_repo(async_session)
    (rec,) = await _seed(
        async_session,
        repo.id,
        "Bare",
    )
    rec.rationale = ""
    rec.decision = ""
    rec.affected_files_json = "[]"
    rec.affected_modules_json = "[]"
    rec.evidence_commits_json = "[]"
    rec.evidence_file = None
    await async_session.flush()

    with pytest.raises(AcceptanceRefusedError) as refusal:
        await accept_decision(async_session, rec, accepter="tester")
    assert len(refusal.value.blockers) == 3

    with pytest.raises(AcceptanceRefusedError):
        await accept_decision(
            async_session, rec, reason="r", scope=["src/a.py"], evidence=["c"]
        )  # no accepter and no artifact

    acceptance = await accept_decision(
        async_session,
        rec,
        accepter="tester",
        reason="a reason",
        scope=["src/a.py"],
        evidence=["deadbeef"],
    )
    assert acceptance.currency == "active"
    assert rec.status == "active"


async def test_the_database_refuses_an_acceptance_with_no_scope(async_session):
    """The contract is a constraint, not only a Python check.

    A caller reaching past the domain function still cannot store an acceptance
    that says nothing about what it governs.
    """
    repo = await insert_repo(async_session)
    (rec,) = await _seed(async_session, repo.id, "Constrained")

    async_session.add(
        DecisionAcceptance(
            repository_id=repo.id,
            decision_id=rec.id,
            seq=1,
            action="accepted",
            currency="active",
            reason="a reason",
            scope_json="[]",
            evidence_json='["c"]',
            accepter="tester",
        )
    )
    with pytest.raises(IntegrityError):
        await async_session.flush()
    await async_session.rollback()


async def test_the_database_refuses_an_anonymous_acceptance(async_session):
    repo = await insert_repo(async_session)
    (rec,) = await _seed(async_session, repo.id, "Anonymous")

    async_session.add(
        DecisionAcceptance(
            repository_id=repo.id,
            decision_id=rec.id,
            seq=1,
            action="accepted",
            currency="active",
            reason="a reason",
            scope_json='["src/a.py"]',
            evidence_json='["c"]',
            accepter="",
            artifact="",
        )
    )
    with pytest.raises(IntegrityError):
        await async_session.flush()
    await async_session.rollback()


# ---------------------------------------------------------------------------
# The review actions
# ---------------------------------------------------------------------------


async def test_the_acceptance_log_appends_rather_than_overwrites(async_session):
    repo = await insert_repo(async_session)
    (rec,) = await _seed(async_session, repo.id, "Reviewed twice")

    await accept_decision(async_session, rec, accepter="tester")
    await return_to_review(async_session, rec, accepter="tester")
    await reaffirm_decision(async_session, rec, accepter="tester")

    rows = (
        (
            await async_session.execute(
                select(DecisionAcceptance)
                .where(DecisionAcceptance.decision_id == rec.id)
                .order_by(DecisionAcceptance.seq)
            )
        )
        .scalars()
        .all()
    )
    assert [(r.seq, r.action) for r in rows] == [
        (1, "accepted"),
        (2, "returned_to_review"),
        (3, "reaffirmed"),
    ]
    assert (await latest_acceptance(async_session, rec.id)).currency == "active"


async def test_returning_to_review_keeps_the_record_a_decision(async_session):
    repo = await insert_repo(async_session)
    (rec,) = await _seed(async_session, repo.id, "Sent back")
    await accept_decision(async_session, rec, accepter="tester")

    await return_to_review(async_session, rec, accepter="tester")

    assert await is_accepted(async_session, rec.id)
    assert await current_currency(async_session, rec) == "needs_review"


async def test_dismissing_an_accepted_decision_withdraws_its_authority(async_session):
    """Otherwise the manifest keeps exporting it as governing."""
    repo = await insert_repo(async_session)
    (rec,) = await _seed(async_session, repo.id, "Withdrawn")
    await accept_decision(async_session, rec, accepter="tester")

    await dismiss_candidate(async_session, rec, reason="no longer true", accepter="tester")

    assert (await latest_acceptance(async_session, rec.id)).currency == "dismissed"
    assert await accepted_decision_ids(
        async_session, repo.id, governing_only=True
    ) == set()


async def test_superseding_writes_an_explicit_edge_and_keeps_the_id_resolving(
    async_session,
):
    repo = await insert_repo(async_session)
    old, new = await _seed(async_session, repo.id, "Old way", "New way")
    await accept_decision(async_session, old, accepter="tester")
    await accept_decision(async_session, new, accepter="tester")

    await supersede_decision(async_session, old, successor_id=new.id, accepter="tester")

    edges = await get_decision_edges(async_session, new.id)
    assert [(e.kind, e.dst_decision_id) for e in edges] == [("supersedes", old.id)]
    assert old.superseded_by == new.id
    assert (await latest_acceptance(async_session, old.id)).currency == "superseded"
    # A superseded decision still resolves to itself: its history is what you
    # asked for when you named it.
    assert await resolve_decision_id(async_session, old.id) == old.id


async def test_merging_folds_a_candidate_and_redirects_its_id(async_session):
    repo = await insert_repo(async_session)
    candidate, target = await _seed(async_session, repo.id, "Duplicate", "Canonical")
    await accept_decision(async_session, target, accepter="tester")

    await merge_candidate(async_session, candidate, into_id=target.id, accepter="tester")

    assert await resolve_decision_id(async_session, candidate.id) == target.id
    # And the folded id cannot be accepted a second time under its own name.
    with pytest.raises(AcceptanceRefusedError):
        await accept_decision(async_session, candidate, accepter="tester")


async def test_merging_refuses_a_candidate_target_and_an_accepted_source(async_session):
    repo = await insert_repo(async_session)
    a, b = await _seed(async_session, repo.id, "A", "B")

    with pytest.raises(ValueError, match="candidate"):
        await merge_candidate(async_session, a, into_id=b.id, accepter="tester")

    await accept_decision(async_session, a, accepter="tester")
    await accept_decision(async_session, b, accepter="tester")
    with pytest.raises(ValueError, match="Supersede"):
        await merge_candidate(async_session, a, into_id=b.id, accepter="tester")


async def test_a_dismissed_candidate_survives_re_extraction(async_session):
    repo = await insert_repo(async_session)
    (rec,) = await _seed(async_session, repo.id, "Rejected idea")
    await dismiss_candidate(async_session, rec, reason="not a real decision")

    await bulk_upsert_decisions(async_session, repo.id, [_dict("Rejected idea")])

    refreshed = await async_session.get(DecisionRecord, rec.id)
    assert refreshed.status == "dismissed"
    assert not await is_accepted(async_session, rec.id)
    open_candidates = await list_candidates(async_session, repo.id, review_state="open")
    assert rec.id not in {r.id for r, _ in open_candidates}


async def test_a_split_request_never_splits_anything(async_session):
    repo = await insert_repo(async_session)
    (rec,) = await _seed(async_session, repo.id, "Two choices in one")

    await request_split(async_session, rec, reason="bundles caching and auth")

    rows = await list_candidates(async_session, repo.id, review_state="needs_split")
    assert [r.id for r, _ in rows] == [rec.id]
    assert rows[0][1].needs_split is True
    # Still exactly one record: machines flag, people split.
    all_rows = (
        (
            await async_session.execute(
                select(DecisionRecord).where(DecisionRecord.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(all_rows) == 1


# ---------------------------------------------------------------------------
# The status column stays a projection
# ---------------------------------------------------------------------------


async def test_update_decision_status_to_active_records_an_acceptance(async_session):
    """The web surface and the CLI must agree about what made a record govern."""
    repo = await insert_repo(async_session)
    (rec,) = await _seed(async_session, repo.id, "Via the API")

    await update_decision_status(async_session, rec.id, "active", accepter="web")

    acceptance = await latest_acceptance(async_session, rec.id)
    assert acceptance is not None and acceptance.accepter == "web"


async def test_update_decision_status_refuses_what_acceptance_refuses(async_session):
    repo = await insert_repo(async_session)
    (rec,) = await _seed(async_session, repo.id, "Unscoped")
    rec.affected_files_json = "[]"
    rec.affected_modules_json = "[]"
    await async_session.flush()

    with pytest.raises(ValueError, match="scope"):
        await update_decision_status(async_session, rec.id, "active", accepter="web")


async def test_effective_currency_reads_the_code_not_only_the_log(async_session):
    repo = await insert_repo(async_session)
    (rec,) = await _seed(async_session, repo.id, "Moved on")
    await accept_decision(async_session, rec, accepter="tester")

    assert await current_currency(async_session, rec) == "active"

    rec.staleness_score = 0.9
    await async_session.flush()
    assert await current_currency(async_session, rec) == "needs_review"

    rec.affected_files_json = json.dumps([])
    rec.affected_modules_json = json.dumps([])
    await async_session.flush()
    assert await current_currency(async_session, rec) == "uncheckable"


async def test_seq_retry_keeps_earlier_acceptances(async_session, monkeypatch):
    """The retry rolls back its own attempt, not the whole session.

    ``decision confirm`` writes many acceptances per session, so a root-scoped
    rollback here would discard every id reviewed before the collision.
    """
    repo = await insert_repo(async_session)
    first, second = await _seed(async_session, repo.id, "First", "Second")
    await accept_decision(async_session, first, accepter="tester")

    real_flush = AsyncSession.flush
    tripped: list[bool] = []

    async def flaky(self, *args, **kwargs):
        pending = [
            obj
            for obj in self.new
            if isinstance(obj, DecisionAcceptance) and obj.decision_id == second.id
        ]
        if pending and not tripped:
            tripped.append(True)
            raise IntegrityError("seq collision", None, Exception("unique"))
        return await real_flush(self, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "flush", flaky)
    await accept_decision(async_session, second, accepter="tester")
    monkeypatch.undo()

    assert tripped, "the collision never fired, so the retry was not exercised"
    assert await latest_acceptance(async_session, first.id) is not None
    assert await latest_acceptance(async_session, second.id) is not None
