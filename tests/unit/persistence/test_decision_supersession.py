"""Phase 3B — auto-supersession & conflict detection.

When a newly-upserted decision is semantically about the same topic as an
existing one (cosine in the related band) and contradicts it, a typed edge is
recorded: ``supersedes`` (auto-flipping the older above the confidence
threshold) for a clear reversal, ``conflicts_with`` for two active decisions
that disagree with no clear winner. A related-but-compatible pair gets nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from repowise.core.analysis.decision_evolution import detect_supersessions_and_conflicts
from repowise.core.persistence.crud import bulk_upsert_decisions, get_decision
from repowise.core.persistence.decision_graph import get_decision_edges
from repowise.core.persistence.vector_store import InMemoryVectorStore
from tests.unit.persistence.helpers import insert_repo


class _TopicEmbedder:
    """Places texts on per-topic axes so same-topic-but-distinct texts land in
    the related band (~0.69 cosine) — below the dedup threshold (so they stay
    two records) but above RELATED_TAU (so they're supersession candidates).
    Cross-topic pairs are near-orthogonal.
    """

    dimensions = 4

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            tl = t.lower()
            if "jwt" in tl:
                v = [1.0, 0.4, 0.0, 0.0]
            elif "session" in tl:
                v = [0.4, 1.0, 0.0, 0.0]
            elif "async" in tl:  # checked before "sync" ("asynchronous" matches both)
                v = [0.0, 0.0, 1.0, 0.4]
            elif "sync" in tl:
                v = [0.0, 0.0, 0.4, 1.0]
            else:
                v = [0.1, 0.1, 0.1, 0.1]
            out.append(v)
        return out


def _dec(title: str, decision: str, *, files: list[str]) -> dict:
    return {
        "title": title,
        "decision": decision,
        "rationale": "",
        "source": "inline_marker",
        "status": "active",
        "affected_files": files,
        "evidence_file": files[0],
        "confidence": 0.6,
        "verification": "exact",
        "source_quote": decision,
    }


async def _backdate(session, decision_id: str, days: int) -> None:
    rec = await get_decision(session, decision_id)
    rec.created_at = datetime.now(UTC) - timedelta(days=days)
    await session.flush()


async def test_reversal_creates_supersedes_edge_and_autoflips(async_session):
    repo = await insert_repo(async_session)
    store = InMemoryVectorStore(_TopicEmbedder())

    old_ids = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            _dec(
                "Use sessions for authentication",
                "use server-side sessions for auth",
                files=["src/auth.py"],
            )
        ],
        vector_store=store,
    )
    new_ids = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            _dec(
                "Adopt JWT authentication",
                "replace sessions with JWT tokens for auth",
                files=["src/auth.py"],
            )
        ],
        vector_store=store,
    )
    old_id, new_id = old_ids[0], new_ids[0]
    # Make the JWT decision unambiguously newer.
    await _backdate(async_session, old_id, days=30)

    summary = await detect_supersessions_and_conflicts(
        async_session,
        repo.id,
        touched_ids=[old_id, new_id],
        vector_store=store,
    )

    assert summary["supersedes"] == 1
    assert summary["flipped"] == 1

    edges = await get_decision_edges(async_session, new_id, direction="out", kinds=("supersedes",))
    assert len(edges) == 1
    assert edges[0].dst_decision_id == old_id
    assert edges[0].confidence >= 0.85  # reviewable confidence

    old = await get_decision(async_session, old_id)
    assert old.status == "superseded"
    assert old.superseded_by == new_id


async def test_two_active_conflicts_without_reversal(async_session):
    repo = await insert_repo(async_session)
    store = InMemoryVectorStore(_TopicEmbedder())

    ids_a = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            _dec(
                "Synchronous worker IO",
                "use synchronous blocking io for the worker",
                files=["src/worker.py"],
            )
        ],
        vector_store=store,
    )
    ids_b = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            _dec(
                "Asynchronous worker IO",
                "use asynchronous non-blocking io for the worker",
                files=["src/worker.py"],
            )
        ],
        vector_store=store,
    )
    a, b = ids_a[0], ids_b[0]

    summary = await detect_supersessions_and_conflicts(
        async_session, repo.id, touched_ids=[a, b], vector_store=store
    )

    assert summary["conflicts"] == 1
    assert summary["supersedes"] == 0
    assert summary["flipped"] == 0
    # Both remain active — a conflict is a smell to surface, not an auto-flip.
    assert (await get_decision(async_session, a)).status == "active"
    assert (await get_decision(async_session, b)).status == "active"


async def test_related_but_compatible_pair_gets_no_edge(async_session):
    repo = await insert_repo(async_session)
    store = InMemoryVectorStore(_TopicEmbedder())

    ids_a = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            _dec(
                "Adopt JWT tokens",
                "adopt JWT tokens for the authentication service",
                files=["src/auth.py"],
            )
        ],
        vector_store=store,
    )
    # Same related band (jwt/session axes), but no opposing verb / reversal and
    # too few shared content tokens → not a contradiction.
    ids_b = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [
            _dec(
                "Adopt session cookies",
                "adopt session cookies for the login flow",
                files=["src/login.py"],
            )
        ],
        vector_store=store,
    )
    a, b = ids_a[0], ids_b[0]

    summary = await detect_supersessions_and_conflicts(
        async_session, repo.id, touched_ids=[a, b], vector_store=store
    )
    assert summary == {"supersedes": 0, "conflicts": 0, "flipped": 0}

    all_edges = await get_decision_edges(async_session, a) + await get_decision_edges(
        async_session, b
    )
    assert all_edges == []


async def test_no_store_is_a_noop(async_session):
    repo = await insert_repo(async_session)
    ids = await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_dec("Use sessions", "use sessions", files=["a.py"])],
    )
    summary = await detect_supersessions_and_conflicts(
        async_session, repo.id, touched_ids=ids, vector_store=None
    )
    assert summary == {"supersedes": 0, "conflicts": 0, "flipped": 0}
