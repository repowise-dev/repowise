"""Phase-2C semantic dedup: paraphrased decisions collapse via the shared
vector store.

Title-string dedup (Phase 1) merges only normalized-title matches. With the
page-generator vector store threaded in, ``bulk_upsert_decisions`` embeds each
decision into that store under a ``decision:`` namespace and matches the
title-unmatched residual against it, so paraphrases ("Use Redis" ≈ "Adopt Redis
cache") fold into one record with two evidence rows. The same co-location is
what makes decisions show up in ``search_codebase``.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select

from repowise.core.analysis.decision_semantic_match import (
    DECISION_VECTOR_PREFIX,
)
from repowise.core.persistence.crud import bulk_upsert_decisions, list_decision_evidence
from repowise.core.persistence.models import DecisionRecord
from repowise.core.persistence.vector_store import InMemoryVectorStore
from tests.unit.persistence.helpers import insert_repo


class _KeywordEmbedder:
    """Deterministic test embedder that aligns texts sharing a keyword.

    Texts mentioning the same concept ("redis" / "postgres") embed to the same
    unit axis (cosine 1.0); everything else hashes into orthogonal-ish dims so
    unrelated decisions stay below the dedup threshold. This isolates the merge
    *wiring* under test from the choice of a real embedding model.
    """

    dimensions = 8

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            tl = t.lower()
            v = [0.0] * self.dimensions
            if "redis" in tl:
                v[0] = 1.0
            elif "postgres" in tl:
                v[1] = 1.0
            else:
                idx = 2 + (int(hashlib.sha256(tl.encode()).hexdigest(), 16) % 6)
                v[idx] = 1.0
            out.append(v)
        return out


def _store() -> InMemoryVectorStore:
    return InMemoryVectorStore(_KeywordEmbedder())


def _decision(title: str, *, source: str, quote: str) -> dict:
    return {
        "title": title,
        "decision": quote,
        "rationale": "",
        "source": source,
        "status": "proposed",
        "evidence_file": f"{title.lower().replace(' ', '_')}.py",
        "confidence": 0.6,
        "verification": "exact",
        "source_quote": quote,
    }


async def _rows(session, repo_id):
    result = await session.execute(
        select(DecisionRecord).where(DecisionRecord.repository_id == repo_id)
    )
    return list(result.scalars().all())


async def test_paraphrase_merges_into_one_record_with_two_evidence_rows(async_session):
    repo = await insert_repo(async_session)
    store = _store()

    # First run establishes the canonical record (and embeds it into the store).
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_decision("Use Redis", source="inline_marker", quote="use Redis for caching")],
        vector_store=store,
    )

    # A harvested paraphrase with a different normalized title arrives. Title
    # dedup can't match it; the store lookup must fold it into the Redis record.
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_decision("Adopt Redis cache", source="llm_inferred", quote="adopt Redis cache")],
        vector_store=store,
    )

    rows = await _rows(async_session, repo.id)
    assert len(rows) == 1, "paraphrased duplicates must collapse into one record"

    evidence = await list_decision_evidence(async_session, rows[0].id)
    assert len(evidence) == 2
    assert {e.source for e in evidence} == {"inline_marker", "llm_inferred"}
    # Headline stays the higher-ranked inline marker, not the llm_inferred paraphrase.
    assert rows[0].source == "inline_marker"


async def test_decisions_are_embedded_into_the_shared_store(async_session):
    repo = await insert_repo(async_session)
    store = _store()

    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_decision("Use Redis", source="inline_marker", quote="use Redis for caching")],
        vector_store=store,
    )

    # Co-located in the page store under the decision namespace → discoverable
    # by search_codebase.
    page_ids = await store.list_page_ids()
    assert any(pid.startswith(DECISION_VECTOR_PREFIX) for pid in page_ids)


async def test_without_store_paraphrase_stays_separate(async_session):
    repo = await insert_repo(async_session)

    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_decision("Use Redis", source="inline_marker", quote="use Redis for caching")],
    )
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_decision("Adopt Redis cache", source="llm_inferred", quote="adopt Redis cache")],
    )

    rows = await _rows(async_session, repo.id)
    assert len(rows) == 2, "title-only dedup cannot collapse paraphrases"


async def test_unrelated_decision_is_not_merged(async_session):
    repo = await insert_repo(async_session)
    store = _store()

    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_decision("Use Redis", source="inline_marker", quote="use Redis for caching")],
        vector_store=store,
    )
    # A genuinely different decision must NOT be folded into the Redis record.
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_decision("Adopt gRPC transport", source="llm_inferred", quote="adopt gRPC")],
        vector_store=store,
    )

    rows = await _rows(async_session, repo.id)
    assert len(rows) == 2


async def test_intra_batch_paraphrases_collapse(async_session):
    repo = await insert_repo(async_session)
    store = _store()

    # Both arrive in the SAME call with different normalized titles. The record
    # created for the first must be visible (embedded) to the second's lookup.
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            _decision("Use Redis", source="inline_marker", quote="use Redis for caching"),
            _decision("Adopt Redis cache", source="llm_inferred", quote="adopt Redis cache"),
        ],
        vector_store=store,
    )

    rows = await _rows(async_session, repo.id)
    assert len(rows) == 1
    evidence = await list_decision_evidence(async_session, rows[0].id)
    assert {e.source for e in evidence} == {"inline_marker", "llm_inferred"}
