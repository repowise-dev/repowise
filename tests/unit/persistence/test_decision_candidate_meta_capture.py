"""Capture writes the review row, and the queue leads with acceptable work.

Before this, ``decision_candidate_meta`` existed only for records the one-time
migration walked, so everything captured since had no lane to filter on and no
ordering signal for the review queue.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from repowise.core import __version__
from repowise.core.persistence.crud import bulk_upsert_decisions, list_decisions
from repowise.core.persistence.crud.authority import (
    accept_decision,
    list_candidates,
    record_blockers,
)
from repowise.core.persistence.models import DecisionCandidateMeta, DecisionRecord
from tests.unit.persistence.helpers import insert_repo


def _decision(title: str, **overrides) -> dict:
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


async def _record(session, title: str) -> DecisionRecord:
    return (
        await session.execute(select(DecisionRecord).where(DecisionRecord.title == title))
    ).scalar_one()


async def test_capture_writes_a_review_row(async_session):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(
        async_session, repo.id, [_decision("Adopt the queue", lane="session_discovery")]
    )

    rec = await _record(async_session, "Adopt the queue")
    meta = await async_session.get(DecisionCandidateMeta, rec.id)
    assert meta is not None
    assert meta.lane == "session_discovery"
    assert meta.extractor_version == __version__
    assert meta.review_state == "open"
    assert meta.scope_unresolved is False
    assert meta.review_priority == 1.0


async def test_lane_falls_back_to_the_source(async_session):
    """Only the session lanes need a discriminator; the rest are their source."""
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(async_session, repo.id, [_decision("From a PR", source="pr")])

    rec = await _record(async_session, "From a PR")
    meta = await async_session.get(DecisionCandidateMeta, rec.id)
    assert meta is not None and meta.lane == "pr"


async def test_lane_filter_matches_a_freshly_captured_candidate(async_session):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            _decision("Broad lane", lane="session_discovery"),
            _decision("Deterministic lane", lane="session"),
        ],
    )

    rows = await list_candidates(async_session, repo.id, lane="session_discovery")
    assert [rec.title for rec, _ in rows] == ["Broad lane"]


async def test_an_unscoped_candidate_is_flagged_and_sorts_last(async_session):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            # Higher confidence, but a reviewer cannot accept it: no scope.
            _decision("No scope", affected_files=[], evidence_file=None, confidence=0.95),
            _decision("Ready to accept", confidence=0.4),
        ],
    )

    unscoped = await _record(async_session, "No scope")
    meta = await async_session.get(DecisionCandidateMeta, unscoped.id)
    assert meta is not None
    assert meta.scope_unresolved is True
    assert meta.review_priority == 0.0
    assert record_blockers(unscoped) == [
        "no scope: name the files or modules it governs",
        "no evidence reference",
    ]

    rows = await list_candidates(async_session, repo.id)
    assert [rec.title for rec, _ in rows] == ["Ready to accept", "No scope"]

    page = await list_decisions(async_session, repo.id, accepted=False)
    assert [rec.title for rec in page] == ["Ready to accept", "No scope"]


async def test_re_extraction_refreshes_priority_without_reopening_review(async_session):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(
        async_session, repo.id, [_decision("Grew scope", affected_files=[], evidence_file=None)]
    )
    rec = await _record(async_session, "Grew scope")
    meta = await async_session.get(DecisionCandidateMeta, rec.id)
    assert meta is not None and meta.review_priority == 0.0
    meta.review_state = "needs_split"
    meta.needs_split = True
    await async_session.flush()

    await bulk_upsert_decisions(async_session, repo.id, [_decision("Grew scope")])

    await async_session.refresh(meta)
    assert meta.review_priority == 1.0
    assert meta.scope_unresolved is False
    # A re-extraction must not walk back what review said about the candidate.
    assert meta.review_state == "needs_split"
    assert meta.needs_split is True


async def test_capture_leaves_an_accepted_record_alone(async_session):
    """Review is over for an accepted record; capture has nothing to say about it."""
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(async_session, repo.id, [_decision("Already governing")])
    rec = await _record(async_session, "Already governing")
    await accept_decision(async_session, rec, accepter="tester")
    meta = await async_session.get(DecisionCandidateMeta, rec.id)
    assert meta is not None and meta.review_state == "accepted"
    meta.review_priority = 0.0
    meta.extractor_version = "0.0.0"
    await async_session.flush()

    await bulk_upsert_decisions(async_session, repo.id, [_decision("Already governing")])

    await async_session.refresh(meta)
    assert meta.review_state == "accepted"
    assert meta.review_priority == 0.0
    assert meta.extractor_version == "0.0.0"


async def test_needs_split_survives_a_capture_that_does_not_claim_it(async_session):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(
        async_session, repo.id, [_decision("Two choices", needs_split=True)]
    )
    rec = await _record(async_session, "Two choices")
    meta = await async_session.get(DecisionCandidateMeta, rec.id)
    assert meta is not None and meta.needs_split is True

    await bulk_upsert_decisions(async_session, repo.id, [_decision("Two choices")])
    await async_session.refresh(meta)
    assert meta.needs_split is True


async def test_the_scope_flag_agrees_with_the_contract_on_blank_entries(async_session):
    """A scope list of one empty string is no scope, and both readers say so."""
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(
        async_session, repo.id, [_decision("Blank scope", affected_files=["  "])]
    )

    rec = await _record(async_session, "Blank scope")
    assert json.loads(rec.affected_files_json) == ["  "]
    meta = await async_session.get(DecisionCandidateMeta, rec.id)
    assert meta is not None and meta.scope_unresolved is True
    assert "no scope: name the files or modules it governs" in record_blockers(rec)


async def test_scope_flag_reads_modules_when_no_files_are_named(async_session):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_decision("Module scoped", affected_files=[], affected_modules=["src"])],
    )

    rec = await _record(async_session, "Module scoped")
    meta = await async_session.get(DecisionCandidateMeta, rec.id)
    assert meta is not None and meta.scope_unresolved is False
    assert json.loads(rec.affected_modules_json) == ["src"]


async def test_an_untouched_candidate_still_gets_its_review_row(async_session):
    """A backlog that predates the review row must not wait to be re-extracted."""
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(async_session, repo.id, [_decision("Older")])
    older = await _record(async_session, "Older")
    meta = await async_session.get(DecisionCandidateMeta, older.id)
    assert meta is not None
    await async_session.delete(meta)
    await async_session.flush()

    # A later run that captures something else entirely.
    await bulk_upsert_decisions(async_session, repo.id, [_decision("Newer")])

    refreshed = await async_session.get(DecisionCandidateMeta, older.id)
    assert refreshed is not None
    assert refreshed.review_priority == 1.0
    assert refreshed.lane == "session"
    # Not extracted by this run, so its provenance is not claimed.
    assert refreshed.extractor_version == ""


async def test_a_single_record_add_gets_a_review_row(async_session):
    """`decision add` goes through upsert_decision, not the accretion path."""
    from repowise.core.persistence.crud import upsert_decision

    repo = await insert_repo(async_session)
    rec = await upsert_decision(
        async_session,
        repository_id=repo.id,
        title="Typed by hand",
        decision="use one queue",
        rationale="because two drifted",
        affected_files=["src/app.py"],
        source="cli",
    )

    meta = await async_session.get(DecisionCandidateMeta, rec.id)
    assert meta is not None
    assert meta.lane == "cli"
    assert meta.review_priority == 1.0


async def test_a_fold_keeps_the_split_flag_the_first_group_raised(async_session):
    """Two titles can land on one record; the later must not drop the flag."""
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            _decision("Cache the parse tree", needs_split=True, lane="session_discovery"),
            _decision("cache  the parse tree"),
        ],
    )

    rec = await _record(async_session, "Cache the parse tree")
    meta = await async_session.get(DecisionCandidateMeta, rec.id)
    assert meta is not None
    assert meta.needs_split is True
    assert meta.lane == "session_discovery"


async def test_a_candidate_with_no_review_row_ranks_as_unjudged(async_session):
    """Not last: it ranks with the refused ones, and confidence orders within."""
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            _decision("Ready", confidence=0.9),
            _decision("Refused", affected_files=[], evidence_file=None, confidence=0.5),
            _decision("Unjudged", confidence=0.7),
        ],
    )
    unjudged = await _record(async_session, "Unjudged")
    await async_session.delete(await async_session.get(DecisionCandidateMeta, unjudged.id))
    await async_session.flush()

    rows = await list_candidates(async_session, repo.id)

    assert [rec.title for rec, _ in rows] == ["Ready", "Unjudged", "Refused"]
