"""Sticky decision statuses across reindexes (#751).

``dismiss`` keeps a tombstone row instead of deleting, and
``bulk_upsert_decisions`` must never walk a human-set status back to
``proposed``: without these guards every reindex resurrected dismissed
comment proposals and flipped confirmed decisions back into the review queue.
Also covers the one-shot purge that drains still-proposed rows of a retired
extraction source.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from repowise.core.persistence.crud import (
    bulk_upsert_decisions,
    get_decision_health_summary,
    list_decision_evidence,
    list_decisions,
    purge_proposed_decisions_by_source,
    update_decision_status,
)
from repowise.core.persistence.models import DecisionEvidence, DecisionRecord
from tests.unit.persistence.helpers import insert_repo


def _decision(title: str, *, source: str = "changelog", status: str = "proposed") -> dict:
    return {
        "title": title,
        "decision": f"{title} because reasons",
        "rationale": "",
        "source": source,
        "status": status,
        "evidence_file": f"{title.lower().replace(' ', '_')}.py",
        "confidence": 0.6,
        "verification": "exact",
        "source_quote": f"{title} because reasons",
    }


async def _only_row(session, repo_id) -> DecisionRecord:
    result = await session.execute(
        select(DecisionRecord).where(DecisionRecord.repository_id == repo_id)
    )
    rows = list(result.scalars().all())
    assert len(rows) == 1
    return rows[0]


async def test_dismissed_decision_survives_reindex_untouched(async_session):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(async_session, repo.id, [_decision("Use Redis")])
    rec = await _only_row(async_session, repo.id)

    dismissed = await update_decision_status(async_session, rec.id, "dismissed")
    assert dismissed is not None and dismissed.status == "dismissed"
    evidence_before = len(await list_decision_evidence(async_session, rec.id))

    # The next reindex re-extracts the identical decision.
    touched = await bulk_upsert_decisions(async_session, repo.id, [_decision("Use Redis")])

    rec = await _only_row(async_session, repo.id)
    assert rec.status == "dismissed"
    assert rec.id not in touched
    # Tombstones accrete nothing.
    assert len(await list_decision_evidence(async_session, rec.id)) == evidence_before


async def test_confirmed_decision_not_walked_back_to_proposed(async_session):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(async_session, repo.id, [_decision("Use Redis")])
    rec = await _only_row(async_session, repo.id)
    await update_decision_status(async_session, rec.id, "active")

    # Re-harvest ties the stored source rank, so the headline branch runs.
    await bulk_upsert_decisions(async_session, repo.id, [_decision("Use Redis")])

    rec = await _only_row(async_session, repo.id)
    assert rec.status == "active"


async def test_proposed_still_upgrades_to_active(async_session):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(async_session, repo.id, [_decision("Use Redis")])

    # An ADR marked accepted arrives for the same decision.
    await bulk_upsert_decisions(
        async_session, repo.id, [_decision("Use Redis", source="adr", status="active")]
    )

    rec = await _only_row(async_session, repo.id)
    assert rec.status == "active"


async def test_update_decision_status_accepts_dismissed(async_session):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(async_session, repo.id, [_decision("Use Redis")])
    rec = await _only_row(async_session, repo.id)
    updated = await update_decision_status(async_session, rec.id, "dismissed")
    assert updated is not None and updated.status == "dismissed"
    with pytest.raises(ValueError):
        await update_decision_status(async_session, rec.id, "nonsense")


async def test_list_decisions_hides_dismissed_by_default(async_session):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(
        async_session, repo.id, [_decision("Use Redis"), _decision("Use Postgres")]
    )
    rows = await list_decisions(async_session, repo.id)
    redis = next(r for r in rows if r.title == "Use Redis")
    await update_decision_status(async_session, redis.id, "dismissed")

    default_titles = {r.title for r in await list_decisions(async_session, repo.id)}
    assert default_titles == {"Use Postgres"}
    dismissed = await list_decisions(async_session, repo.id, status="dismissed")
    assert {r.title for r in dismissed} == {"Use Redis"}

    summary = (await get_decision_health_summary(async_session, repo.id))["summary"]
    assert summary["dismissed"] == 1
    assert all(
        d.title != "Use Redis"
        for d in (await get_decision_health_summary(async_session, repo.id))[
            "proposed_awaiting_review"
        ]
    )


async def test_purge_drains_proposed_rows_of_source_only(async_session):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            _decision("Legacy comment one", source="code_comment"),
            _decision("Legacy comment two", source="code_comment"),
            _decision("Real changelog decision", source="changelog"),
        ],
    )
    rows = await list_decisions(async_session, repo.id)
    keeper = next(r for r in rows if r.title == "Legacy comment two")
    await update_decision_status(async_session, keeper.id, "active")

    deleted = await purge_proposed_decisions_by_source(async_session, repo.id, "code_comment")
    assert deleted == 1

    remaining = {r.title for r in await list_decisions(async_session, repo.id)}
    assert remaining == {"Legacy comment two", "Real changelog decision"}

    # Child evidence rows of the purged record are gone too.
    orphan_evidence = await async_session.execute(
        select(DecisionEvidence)
        .join(
            DecisionRecord,
            DecisionEvidence.decision_id == DecisionRecord.id,
            isouter=True,
        )
        .where(DecisionRecord.id.is_(None))
    )
    assert list(orphan_evidence.scalars().all()) == []
