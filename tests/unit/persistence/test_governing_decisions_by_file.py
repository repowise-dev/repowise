"""Which decisions govern a file, read in bulk for the refactoring plan annotations."""

from __future__ import annotations

from sqlalchemy import select

from repowise.core.persistence.crud import bulk_upsert_decisions
from repowise.core.persistence.crud.authority import (
    accept_decision,
    current_currency,
    dismiss_candidate,
    governing_decisions_by_file,
    return_to_review,
    supersede_decision,
)
from repowise.core.persistence.models import DecisionRecord
from tests.unit.persistence.helpers import insert_repo
from tests.unit.persistence.test_decision_authority import _dict


async def test_only_accepted_governing_decisions_govern_the_files_they_name(async_session):
    repo = await insert_repo(async_session)
    titles = ("Active", "Review", "Candidate", "Superseded", "Withdrawn", "Footprint", "Successor")
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            _dict(title, affected_files=[f"src/{t}.py"], evidence_file=f"src/{t}.py")
            for title in titles
            if (t := title.lower())
        ],
    )
    stored = (
        await async_session.execute(
            select(DecisionRecord).where(DecisionRecord.repository_id == repo.id)
        )
    ).scalars()
    by_title = {record.title: record for record in stored}
    rows = [by_title[title] for title in titles]
    active, review, _candidate, superseded, withdrawn, footprint, successor = rows
    for record in (active, review, superseded, withdrawn, footprint, successor):
        await accept_decision(async_session, record, accepter="tester")
    await return_to_review(async_session, review, accepter="tester")
    await supersede_decision(async_session, superseded, successor_id=successor.id, accepter="tester")
    await dismiss_candidate(async_session, withdrawn, accepter="tester")
    # A commit's footprint is not a claim about those files.
    footprint.scope_basis = "commit_footprint"
    await async_session.flush()
    assert await current_currency(async_session, review) == "needs_review"

    governed = await governing_decisions_by_file(async_session, repo.id)

    # ``needs_review`` still binds: its code moved, so it is re-read, not ignored.
    assert set(governed) == {"src/active.py", "src/review.py", "src/successor.py"}
    assert governed["src/active.py"] == [(active.id, "Active")]
    # Files are named exactly; the derived module does not govern its directory.
    assert "src" not in governed


async def test_recent_commits_leave_out_a_shallow_boundary_and_short_history(async_session):
    from datetime import UTC, datetime, timedelta

    from repowise.core.analysis.health.refactoring.annotations import load_annotation_facts
    from repowise.core.persistence.models import GitCommit

    repo = await insert_repo(async_session)
    head = datetime(2026, 10, 1, tzinfo=UTC)

    def commit(sha: str, days_ago: int) -> GitCommit:
        return GitCommit(
            repository_id=repo.id,
            sha=sha,
            author_name="dev",
            author_email="dev@example.com",
            committed_at=head - timedelta(days=days_ago),
        )

    async_session.add_all([commit("new", 0), commit("old", 10)])
    await async_session.flush()
    # Ten days of stored history cannot tell recent from all of it.
    assert (await load_annotation_facts(async_session, repo.id, [])).recent is None

    # A depth-limited clone: its boundary commit is the oldest row, which blame
    # credits with every older line; the span guard keeps it out of the window.
    async_session.add(commit("boundary", 30))
    await async_session.flush()
    recent = (await load_annotation_facts(async_session, repo.id, [])).recent
    assert recent is not None and set(recent) == {"new", "old"}
