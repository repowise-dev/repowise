"""Phase 1 item 1: a transcript takes the headline, and old rows are re-stamped.

Two halves of the same change. The ladder swap is what lets a user's own words
win; ``reconcile_source_ranks`` is what stops a store from holding rows on both
ladders at once, which would leave headline confidence derived from a mixture.
"""

from __future__ import annotations

from sqlalchemy import select

from repowise.core.analysis.decision_provenance import (
    RETIRED_SOURCES,
    compute_confidence,
    rank_for_source,
)
from repowise.core.persistence.crud import (
    bulk_upsert_decisions,
    list_decision_evidence,
    purge_proposed_decisions_by_source,
    reconcile_source_ranks,
)
from repowise.core.persistence.models import DecisionEvidence, DecisionRecord
from tests.unit.persistence.helpers import accept, insert_repo

_TITLE = "Use PostgreSQL for storage"


def _adr_dict():
    return {
        "title": _TITLE,
        "decision": "Use PostgreSQL as the primary datastore",
        "rationale": "Strong transactional guarantees",
        "source": "adr",
        "status": "active",
        "evidence_file": "docs/adr/0001-postgres.md",
        "confidence": 0.9,
        "verification": "exact",
        "source_quote": "Use PostgreSQL as the primary datastore",
    }


def _session_dict():
    return {
        "title": _TITLE,
        "decision": "Went with Postgres because we need real transactions",
        "rationale": "the user said so, in their own words, mid-task",
        "source": "session",
        "status": "active",
        "confidence": 0.8,
        "verification": "exact",
        "source_quote": "Went with Postgres because we need real transactions",
    }


async def _record(session, repo_id):
    result = await session.execute(
        select(DecisionRecord).where(DecisionRecord.repository_id == repo_id)
    )
    return result.scalars().one()


async def test_session_takes_the_headline_from_an_adr(async_session):
    """The bug this item exists for: the ADR landed first and used to keep it."""
    repo = await insert_repo(async_session)

    await bulk_upsert_decisions(async_session, repo.id, [_adr_dict()])
    await bulk_upsert_decisions(async_session, repo.id, [_session_dict()])

    rec = await _record(async_session, repo.id)
    assert rec.source == "session"
    assert rec.decision == "Went with Postgres because we need real transactions"

    # The ADR is corroboration, never discarded.
    sources = {e.source for e in await list_decision_evidence(async_session, rec.id)}
    assert sources == {"adr", "session"}


async def test_adr_does_not_take_the_headline_back(async_session):
    """Re-harvesting the document on a later run must not undo the promotion."""
    repo = await insert_repo(async_session)

    await bulk_upsert_decisions(async_session, repo.id, [_session_dict()])
    await bulk_upsert_decisions(async_session, repo.id, [_adr_dict()])

    rec = await _record(async_session, repo.id)
    assert rec.source == "session"


async def test_reconcile_restamps_rows_left_on_the_old_ladder(async_session):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(async_session, repo.id, [_session_dict()])
    rec = await _record(async_session, repo.id)

    # Simulate a store written before the swap: session evidence stamped 7, and
    # the headline confidence derived from that stale rank.
    row = (
        await async_session.execute(
            select(DecisionEvidence).where(DecisionEvidence.decision_id == rec.id)
        )
    ).scalars().one()
    row.source_rank = 7
    rec.confidence = compute_confidence(7, 1, "exact")
    await async_session.flush()
    stale_confidence = rec.confidence

    moved = await reconcile_source_ranks(async_session)

    assert moved == 1
    await async_session.refresh(row)
    await async_session.refresh(rec)
    assert row.source_rank == rank_for_source("session") == 8
    assert rec.confidence == compute_confidence(8, 1, "exact")
    assert rec.confidence > stale_confidence


async def test_reconcile_scores_a_headline_the_same_way_the_upsert_does(async_session):
    """The two writers must not drift; they share ``_rederive_headline``.

    Take a record scored by the upsert path, push its evidence back onto the old
    ladder, reconcile, and the score has to land back on the same number. If the
    two ever grow separate copies of the expression, this is what catches it.
    """
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(async_session, repo.id, [_session_dict(), _adr_dict()])
    rec = await _record(async_session, repo.id)
    scored_by_upsert = rec.confidence

    rows = (
        await async_session.execute(
            select(DecisionEvidence).where(DecisionEvidence.decision_id == rec.id)
        )
    ).scalars().all()
    for row in rows:
        row.source_rank = 7 if row.source == "session" else 8
    rec.confidence = 0.123
    await async_session.flush()

    assert await reconcile_source_ranks(async_session) == len(rows)
    await async_session.refresh(rec)
    assert rec.confidence == scored_by_upsert


async def test_reconcile_is_idempotent(async_session):
    """A reconciled store pays one scan and writes nothing; it runs every persist."""
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(async_session, repo.id, [_session_dict(), _adr_dict()])

    assert await reconcile_source_ranks(async_session) == 0
    assert await reconcile_source_ranks(async_session) == 0


async def test_every_retired_source_is_drained(async_session):
    """Item 2: removing the extractor is half the job; the rows have to go too.

    Driven off ``RETIRED_SOURCES`` rather than a literal list, so a fourth
    removal is covered here the day it lands instead of the day someone notices
    the queue never got shorter.
    """
    repo = await insert_repo(async_session)
    for i, source in enumerate(RETIRED_SOURCES):
        await bulk_upsert_decisions(
            async_session,
            repo.id,
            [
                {
                    "title": f"Retired record {i}",
                    "decision": f"something {source} once mined",
                    "source": source,
                    "status": "proposed",
                    "confidence": 0.6,
                    "verification": "exact",
                    "source_quote": f"something {source} once mined",
                }
            ],
        )

    for source in RETIRED_SOURCES:
        assert await purge_proposed_decisions_by_source(async_session, repo.id, source) == 1

    remaining = (
        await async_session.execute(
            select(DecisionRecord).where(DecisionRecord.repository_id == repo.id)
        )
    ).scalars().all()
    assert remaining == []


def test_persist_wires_both_repairs():
    """Both repairs sit inside ``except Exception: logger.debug`` on the persist
    path, so a bad import degrades to a silent no-op that no other test would
    notice. Assert the names resolve from where ``persist_analysis`` imports
    them: ``RETIRED_SOURCES`` in particular reaches it through a star-exporting
    façade and is one missing ``__all__`` entry away from vanishing quietly.
    """
    from repowise.core.analysis import decision_provenance as facade
    from repowise.core.persistence.crud import (
        purge_proposed_decisions_by_source,
        reconcile_source_ranks,
    )

    assert facade.RETIRED_SOURCES == RETIRED_SOURCES
    assert callable(purge_proposed_decisions_by_source)
    assert callable(reconcile_source_ranks)


async def test_purge_keeps_what_a_human_confirmed(async_session):
    """Only ``proposed`` rows drain. An accepted record survives its source's removal."""
    repo = await insert_repo(async_session)
    ids = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            {
                "title": "Kept because someone confirmed it",
                "decision": "mined from a changelog, then confirmed by a human",
                "source": "changelog",
                "status": "proposed",
                "affected_files": ["src/changelog.py"],
                "evidence_file": "CHANGELOG.md",
                "confidence": 0.6,
                "verification": "exact",
                "source_quote": "mined from a changelog, then confirmed by a human",
            }
        ],
    )
    # Accepting is what makes it survive; the row lands ``proposed`` whatever
    # the extraction dict claimed.
    await accept(async_session, ids[0])

    assert await purge_proposed_decisions_by_source(async_session, repo.id, "changelog") == 0
    rec = await _record(async_session, repo.id)
    assert rec.status == "active"
