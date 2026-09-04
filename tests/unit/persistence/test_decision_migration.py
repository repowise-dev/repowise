"""Classifying pre-split decision rows.

The store this migrates from could not tell a decision from a candidate, so the
question every test here asks is the same one: does this row carry an acceptance
event somebody performed, or does it only carry a status a machine wrote?
"""

from __future__ import annotations

from sqlalchemy import select

from repowise.core.persistence.crud.authority import (
    accept_decision,
    is_accepted,
    request_split,
)
from repowise.core.persistence.decision_migration import (
    apply_migration,
    plan_migration,
    render_plan,
)
from repowise.core.persistence.models import DecisionCandidateMeta, DecisionRecord
from tests.unit.persistence.helpers import insert_repo


async def _legacy(
    session,
    repo_id: str,
    title: str,
    *,
    status: str = "active",
    source: str = "session",
    rationale: str = "because it was measured faster",
    files: list[str] | None = None,
) -> DecisionRecord:
    """Insert a row shaped the way the pre-split store held them.

    Deliberately built by hand rather than through the write path: the point is
    a store nothing in this build could produce any more.
    """
    rec = DecisionRecord(
        repository_id=repo_id,
        title=title,
        decision=f"{title}: do the thing",
        rationale=rationale,
        status=status,
        source=source,
        affected_files_json='["src/app.py"]' if files is None else f"{files!r}".replace("'", '"'),
        evidence_file="src/app.py",
        confidence=0.84,
    )
    session.add(rec)
    await session.flush()
    return rec


async def _plan_for(session, repo_id, decision_id):
    plan = await plan_migration(session, repo_id)
    return next(r for r in plan.rows if r.decision_id == decision_id)


async def test_an_automatically_promoted_row_becomes_a_candidate(async_session):
    """Recurrence promoted these; nobody accepted them."""
    repo = await insert_repo(async_session)
    rec = await _legacy(async_session, repo.id, "Promoted by recurrence")

    row = await _plan_for(async_session, repo.id, rec.id)

    assert row.outcome == "candidate"
    assert "not by a person" in row.reason


async def test_a_proposal_becomes_a_candidate(async_session):
    repo = await insert_repo(async_session)
    rec = await _legacy(async_session, repo.id, "Awaiting review", status="proposed")

    row = await _plan_for(async_session, repo.id, rec.id)

    assert row.outcome == "candidate"
    assert row.reason == "never accepted: it was awaiting review"


async def test_a_self_authored_row_keeps_its_authority(async_session):
    """A ``cli`` row exists because somebody typed it."""
    repo = await insert_repo(async_session)
    rec = await _legacy(async_session, repo.id, "Typed by hand", source="cli")

    row = await _plan_for(async_session, repo.id, rec.id)
    assert row.outcome == "decision"

    await apply_migration(async_session, repo.id)
    assert await is_accepted(async_session, rec.id)


async def test_a_self_authored_row_missing_its_parts_is_still_demoted(async_session):
    repo = await insert_repo(async_session)
    rec = await _legacy(
        async_session, repo.id, "Typed but empty", source="cli", rationale="", files=[]
    )
    rec.decision = ""
    await async_session.flush()

    row = await _plan_for(async_session, repo.id, rec.id)

    assert row.outcome == "candidate"
    assert "no rationale" in row.reason and "no scope" in row.reason


async def test_a_retirement_is_preserved_rather_than_reopened(async_session):
    """Reopening one would put a decision the user retired back in the queue."""
    repo = await insert_repo(async_session)
    dismissed = await _legacy(async_session, repo.id, "Rejected", status="dismissed")
    deprecated = await _legacy(async_session, repo.id, "Retired", status="deprecated")
    superseded = await _legacy(async_session, repo.id, "Replaced", status="superseded")

    await apply_migration(async_session, repo.id)

    for rec, status in (
        (dismissed, "dismissed"),
        (deprecated, "deprecated"),
        (superseded, "superseded"),
    ):
        refreshed = await async_session.get(DecisionRecord, rec.id)
        assert refreshed.status == status
        meta = await async_session.get(DecisionCandidateMeta, rec.id)
        assert meta.review_state == "dismissed"


async def test_apply_is_idempotent(async_session):
    repo = await insert_repo(async_session)
    await _legacy(async_session, repo.id, "One")
    await _legacy(async_session, repo.id, "Two", source="cli")
    await _legacy(async_session, repo.id, "Three", status="dismissed")

    first = await apply_migration(async_session, repo.id)
    second = await apply_migration(async_session, repo.id)

    assert first.counts() == {"candidate": 1, "decision": 1, "tombstone": 1}
    assert second.counts() == {"candidate": 1, "already_migrated": 1, "tombstone": 1}
    rows = (
        (
            await async_session.execute(
                select(DecisionRecord).where(DecisionRecord.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    )
    assert sorted(r.status for r in rows) == ["active", "dismissed", "proposed"]


async def test_rerunning_does_not_reopen_a_review_action(async_session):
    """A split request survives the next index run."""
    repo = await insert_repo(async_session)
    rec = await _legacy(async_session, repo.id, "Bundles two choices")
    await apply_migration(async_session, repo.id)
    await request_split(async_session, rec, reason="two choices")

    await apply_migration(async_session, repo.id)

    meta = await async_session.get(DecisionCandidateMeta, rec.id)
    assert meta.review_state == "needs_split"


async def test_an_accepted_record_is_left_alone(async_session):
    repo = await insert_repo(async_session)
    rec = await _legacy(async_session, repo.id, "Already accepted", status="proposed")
    await accept_decision(async_session, rec, accepter="tester")

    row = await _plan_for(async_session, repo.id, rec.id)

    assert row.outcome == "already_migrated"
    await apply_migration(async_session, repo.id)
    assert (await async_session.get(DecisionRecord, rec.id)).status == "active"


async def test_duplicates_are_clustered_but_never_merged(async_session):
    """Exact re-extractions only, and flagged rather than folded together."""
    repo = await insert_repo(async_session)
    first = await _legacy(async_session, repo.id, "Use Redis")
    second = await _legacy(async_session, repo.id, "use redis!")
    unrelated = await _legacy(async_session, repo.id, "Cache with Redis")

    plan = await plan_migration(async_session, repo.id)

    assert plan.duplicate_clusters == {first.id: [second.id]}
    assert unrelated.id not in plan.duplicate_clusters
    await apply_migration(async_session, repo.id, plan=plan)
    remaining = (
        (
            await async_session.execute(
                select(DecisionRecord).where(DecisionRecord.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(remaining) == 3


async def test_the_report_accounts_for_every_row(async_session):
    repo = await insert_repo(async_session)
    await _legacy(async_session, repo.id, "One")
    await _legacy(async_session, repo.id, "Two", status="proposed")
    await _legacy(async_session, repo.id, "Three", source="cli")

    plan = await plan_migration(async_session, repo.id)
    report = render_plan(plan)

    assert sum(plan.counts().values()) == len(plan.rows) == 3
    assert "Total legacy records" in report
    assert all(row.reason for row in plan.rows)
