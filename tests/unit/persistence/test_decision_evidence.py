"""bulk_upsert_decisions: provenance accretes into decision_evidence rows."""

from __future__ import annotations

from sqlalchemy import select

from repowise.core.analysis.decision_provenance import compute_confidence, rank_for_source
from repowise.core.persistence.crud import bulk_upsert_decisions, list_decision_evidence
from repowise.core.persistence.models import DecisionRecord
from tests.unit.persistence.helpers import insert_repo


def _adr_dict(title="Use PostgreSQL for storage"):
    return {
        "title": title,
        "decision": "Use PostgreSQL as the primary datastore",
        "rationale": "Strong transactional guarantees",
        "source": "adr",
        "status": "active",
        "evidence_file": "docs/adr/0001-postgres.md",
        "confidence": 0.9,
        "verification": "exact",
        "source_quote": "Use PostgreSQL as the primary datastore",
    }


def _commit_dict(title="use postgresql for storage!"):
    # Normalizes to the same title as the ADR (case/punctuation-insensitive).
    # Semantic ("Postgres" ≈ "PostgreSQL") matching is Phase 2; Phase 1 merges
    # on normalized title only.
    return {
        "title": title,
        "decision": "switched the datastore to PostgreSQL",
        "rationale": "inferred from commit",
        "source": "git_archaeology",
        "status": "proposed",
        "evidence_commits": ["abc1234"],
        "confidence": 0.7,
        "verification": "fuzzy",
        "source_quote": "migrate to PostgreSQL",
    }


async def _decision_rows(session, repo_id):
    result = await session.execute(
        select(DecisionRecord).where(DecisionRecord.repository_id == repo_id)
    )
    return list(result.scalars().all())


async def test_two_sources_merge_into_one_record_with_two_evidence_rows(async_session):
    repo = await insert_repo(async_session)

    await bulk_upsert_decisions(async_session, repo.id, [_adr_dict(), _commit_dict()])

    rows = await _decision_rows(async_session, repo.id)
    assert len(rows) == 1, "ADR + commit describing the same decision must merge"
    rec = rows[0]

    evidence = await list_decision_evidence(async_session, rec.id)
    assert len(evidence) == 2
    assert {e.source for e in evidence} == {"adr", "git_archaeology"}

    # Headline comes from the highest-ranked source (ADR > commit).
    assert rec.source == "adr"
    assert rec.decision == "Use PostgreSQL as the primary datastore"
    assert rec.status == "active"

    # Strongest evidence verification wins; confidence reflects corroboration.
    assert rec.verification == "exact"
    # Two corroborating sources score strictly above the same decision backed
    # by a single source at the same top rank.
    solo = compute_confidence(rank_for_source("adr"), corroboration_count=1, verification="exact")
    assert rec.confidence > solo


async def test_evidence_upsert_is_idempotent(async_session):
    repo = await insert_repo(async_session)

    await bulk_upsert_decisions(async_session, repo.id, [_adr_dict(), _commit_dict()])
    await bulk_upsert_decisions(async_session, repo.id, [_adr_dict(), _commit_dict()])

    rows = await _decision_rows(async_session, repo.id)
    assert len(rows) == 1
    evidence = await list_decision_evidence(async_session, rows[0].id)
    assert len(evidence) == 2, "re-indexing the same sources must converge, not duplicate"


async def test_incremental_source_appends_third_evidence_row(async_session):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(async_session, repo.id, [_adr_dict(), _commit_dict()])

    # A later run surfaces the same decision from a CHANGELOG entry.
    changelog = {
        "title": "Use PostgreSQL  for  storage!",  # same normalized title
        "decision": "Use PostgreSQL as the primary datastore",
        "source": "changelog",
        "status": "proposed",
        "evidence_file": "CHANGELOG.md",
        "confidence": 0.6,
        "verification": "exact",
        "source_quote": "Use PostgreSQL as the primary datastore",
    }
    await bulk_upsert_decisions(async_session, repo.id, [changelog])

    rows = await _decision_rows(async_session, repo.id)
    assert len(rows) == 1
    evidence = await list_decision_evidence(async_session, rows[0].id)
    assert {e.source for e in evidence} == {"adr", "git_archaeology", "changelog"}
    # Highest rank is still ADR; corroboration now spans three sources.
    assert rows[0].source == "adr"


async def test_distinct_decisions_stay_separate(async_session):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_adr_dict(title="Use PostgreSQL"), _adr_dict(title="Adopt gRPC internally")],
    )
    rows = await _decision_rows(async_session, repo.id)
    assert len(rows) == 2
