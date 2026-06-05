"""Batched vector-store operations: pgvector executemany + IN-filtered
summary lookups (pgvector, LanceDB)."""

from __future__ import annotations

import pytest

from repowise.core.persistence.vector_store.lancedb_store import _paths_in_filter
from repowise.core.persistence.vector_store.pgvector_store import (
    PgVectorStore,
    _summary_payload,
)
from repowise.core.providers.embedding.base import MockEmbedder

# ---------------------------------------------------------------------------
# Fake async-SQLAlchemy session plumbing
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple]:
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Records every execute() call; serves canned rows."""

    def __init__(self, rows: list[tuple] | None = None) -> None:
        self.executed: list[tuple[str, object]] = []
        self.commits = 0
        self._rows = rows or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        return _FakeResult(self._rows)

    async def commit(self):
        self.commits += 1


def _factory(session: _FakeSession):
    def make() -> _FakeSession:
        return session

    return make


# ---------------------------------------------------------------------------
# pgvector embed_batch — one executemany, not N round-trips
# ---------------------------------------------------------------------------


async def test_pg_embed_batch_single_executemany() -> None:
    session = _FakeSession()
    store = PgVectorStore(_factory(session), MockEmbedder())

    await store.embed_batch(
        [("p1", "alpha", {}), ("p2", "beta", {}), ("p3", "gamma", {})]
    )

    assert len(session.executed) == 1  # one statement, list-of-params
    stmt, params = session.executed[0]
    assert "UPDATE wiki_pages SET embedding" in stmt
    assert isinstance(params, list) and len(params) == 3
    assert [p["pid"] for p in params] == ["p1", "p2", "p3"]
    assert all(p["emb"].startswith("[") for p in params)
    assert session.commits == 1


async def test_pg_embed_batch_empty_is_noop() -> None:
    session = _FakeSession()
    store = PgVectorStore(_factory(session), MockEmbedder())
    await store.embed_batch([])
    assert session.executed == []


# ---------------------------------------------------------------------------
# pgvector get_page_summaries_by_paths — one IN query
# ---------------------------------------------------------------------------


async def test_pg_batch_summaries_single_query_first_row_wins() -> None:
    rows = [
        ("a.py", "Summary of a", {"exports": ["A"]}),
        ("a.py", "duplicate row ignored", {}),
        ("b.py", "Summary of b", '{"exports": ["B1", "B2"]}'),
        ("c.py", "", {}),  # empty summary dropped
    ]
    session = _FakeSession(rows=rows)
    store = PgVectorStore(_factory(session), MockEmbedder())

    out = await store.get_page_summaries_by_paths(["a.py", "b.py", "c.py"])

    assert len(session.executed) == 1
    stmt, params = session.executed[0]
    assert "target_path IN" in stmt
    assert params == {"paths": ["a.py", "b.py", "c.py"]}
    assert out == {
        "a.py": {"summary": "Summary of a", "key_exports": ["A"]},
        "b.py": {"summary": "Summary of b", "key_exports": ["B1", "B2"]},
    }


async def test_pg_batch_summaries_empty_paths() -> None:
    session = _FakeSession()
    store = PgVectorStore(_factory(session), MockEmbedder())
    assert await store.get_page_summaries_by_paths([]) == {}
    assert session.executed == []


def test_summary_payload_matches_single_path_parsing() -> None:
    assert _summary_payload("x" * 600, {"exports": ["E"]}) == {
        "summary": "x" * 500,
        "key_exports": ["E"],
    }
    assert _summary_payload(None, "not json") == {"summary": "", "key_exports": []}


# ---------------------------------------------------------------------------
# LanceDB IN-filter escaping + optional round-trip
# ---------------------------------------------------------------------------


def test_lancedb_paths_filter_escapes_quotes() -> None:
    flt = _paths_in_filter(["a.py", "weird'name.py"])
    assert flt == "target_path IN ('a.py', 'weird''name.py')"


@pytest.mark.asyncio
async def test_lancedb_batch_summaries_roundtrip(tmp_path) -> None:
    pytest.importorskip("lancedb")
    from repowise.core.persistence.vector_store import LanceDBVectorStore

    store = LanceDBVectorStore(str(tmp_path / "lance"), MockEmbedder())
    try:
        await store.embed_batch(
            [
                ("p1", "Summary text for module a", {"target_path": "a.py"}),
                ("p2", "Summary text for module b", {"target_path": "b.py"}),
                ("p3", "Summary text for module c", {"target_path": "c.py"}),
            ]
        )
        batch = await store.get_page_summaries_by_paths(["a.py", "c.py", "missing.py"])
        singles = {
            p: await store.get_page_summary_by_path(p) for p in ("a.py", "c.py")
        }
        assert set(batch) == {"a.py", "c.py"}
        for p in ("a.py", "c.py"):
            assert batch[p]["summary"] == singles[p]["summary"]
    finally:
        await store.close()
