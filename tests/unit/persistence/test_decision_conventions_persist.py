"""A conventions record keeps its own conformance score and its own identity.

The source measures how many files bypass the wrapper and writes that as the
record's staleness; the git-diff recompute must not overwrite it, and the
semantic dedup must not fold a counted record into prose that sounds alike.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from repowise.core.persistence.crud import bulk_upsert_decisions, recompute_decision_staleness
from repowise.core.persistence.models import DecisionRecord, GitMetadata
from tests.unit.persistence.helpers import insert_repo
from tests.unit.persistence.test_decision_semantic_dedup import _decision, _store


def _convention(title: str = "redis goes through db/client.py", **overrides) -> dict:
    return {
        "title": title,
        "decision": "12 of 13 files reach redis through db/client.py; 1 import it directly.",
        "rationale": "Counted from import edges.",
        "source": "conventions",
        "status": "proposed",
        "evidence_file": "db/client.py",
        "affected_files": ["db/client.py", "jobs/raw.py"],
        "confidence": 0.7,
        "verification": "exact",
        "source_quote": "12 of 13 files reach redis through db/client.py; 1 import it directly.",
        "staleness_score": 0.077,
        **overrides,
    }


async def _rows(session, repo_id):
    result = await session.execute(
        select(DecisionRecord).where(DecisionRecord.repository_id == repo_id)
    )
    return list(result.scalars().all())


async def test_the_supplied_score_is_written_on_create(async_session):
    repo = await insert_repo(async_session)

    await bulk_upsert_decisions(async_session, repo.id, [_convention()])

    (rec,) = await _rows(async_session, repo.id)
    assert rec.staleness_score == pytest.approx(0.077)


async def test_a_re_extraction_moves_the_score_with_the_pattern(async_session):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(async_session, repo.id, [_convention()])

    await bulk_upsert_decisions(
        async_session, repo.id, [_convention(staleness_score=0.25)]
    )

    (rec,) = await _rows(async_session, repo.id)
    assert rec.staleness_score == pytest.approx(0.25)


async def test_a_record_without_a_score_still_starts_at_zero(async_session):
    repo = await insert_repo(async_session)

    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_decision("Use Redis", source="inline_marker", quote="use Redis for caching")],
    )

    (rec,) = await _rows(async_session, repo.id)
    assert rec.staleness_score == 0.0


async def test_the_git_recompute_leaves_a_conventions_score_alone(async_session):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(async_session, repo.id, [_convention()])
    (rec,) = await _rows(async_session, repo.id)
    rec.created_at = datetime.now(UTC) - timedelta(days=30)
    async_session.add(
        GitMetadata(
            repository_id=repo.id,
            file_path="db/client.py",
            commit_count_90d=3,
            last_commit_at=datetime.now(UTC),
        )
    )
    await async_session.flush()

    updated = await recompute_decision_staleness(
        async_session, repo.id, {"jobs/raw.py": {"last_commit_at": datetime.now(UTC)}}
    )

    refreshed = await async_session.get(DecisionRecord, rec.id)
    assert updated == 0
    assert refreshed.staleness_score == pytest.approx(0.077)
    # The module backfill still applies to it.
    assert json.loads(refreshed.affected_modules_json) == ["db", "jobs"]


async def test_a_counted_record_does_not_fold_into_prose_that_sounds_alike(async_session):
    repo = await insert_repo(async_session)
    store = _store()
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_decision("Use Redis", source="inline_marker", quote="use Redis for caching")],
        vector_store=store,
    )

    # The keyword embedder puts both texts on the same axis, so a prose
    # record with this title would have merged.
    await bulk_upsert_decisions(
        async_session, repo.id, [_convention()], vector_store=store
    )

    rows = await _rows(async_session, repo.id)
    assert sorted(r.source for r in rows) == ["conventions", "inline_marker"]


async def test_prose_that_sounds_alike_still_folds_without_the_source(async_session):
    """Ablation partner: the skip is keyed on the source, not on the title."""
    repo = await insert_repo(async_session)
    store = _store()
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_decision("Use Redis", source="inline_marker", quote="use Redis for caching")],
        vector_store=store,
    )

    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_decision("Adopt Redis cache", source="adr", quote="adopt Redis cache")],
        vector_store=store,
    )

    assert len(await _rows(async_session, repo.id)) == 1
