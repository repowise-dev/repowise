"""Answer-cache lifecycle: upsert on re-synthesis, commit/TTL invalidation.

Regression coverage for the silent-failure mode where the cache write was a
plain INSERT under ``suppress(Exception)``: every bypass-and-resynthesize
round (hedged rows, schema bumps) violated ``uq_answer_cache_q`` and failed
silently, so a bad cached row was permanent. The write is now a
delete-then-insert upsert, and reads invalidate on indexed-commit change or
hard TTL.
"""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from repowise.core.persistence.models import AnswerCache, Repository

QUESTION = "how does the auth service work"

# Top score >= 3.0 with an absolute gap >= 0.5 → dominant → synthesis runs.
_HITS = [
    {"page_id": "file_page:src/auth/service.py", "score": 5.0},
    {"page_id": "file_page:src/auth/middleware.py", "score": 4.0},
]


def _patch_retrieval(monkeypatch, answer_mod):
    async def _fake_retrieve(question, ctx):
        return [dict(h) for h in _HITS]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for h in hits:
            h["target_path"] = h["page_id"].removeprefix("file_page:")
            h["title"] = h["target_path"]
            h["summary"] = ""
            h["snippet"] = ""
            h["page_type"] = "file_page"
        return hits

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)


class _Provider:
    provider_name = "mock"
    model_name = "mock-1"

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def generate(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(content=self.content)


def _patch_provider(monkeypatch, answer_mod, provider: _Provider) -> None:
    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: provider)


async def _cache_rows(factory) -> list[AnswerCache]:
    from repowise.core.persistence.database import get_session

    async with get_session(factory) as session:
        res = await session.execute(select(AnswerCache))
        return list(res.scalars().all())


@pytest.mark.asyncio
async def test_hedged_row_is_upgraded_on_resynthesis(setup_mcp, factory, monkeypatch):
    """Hedged cached answer → bypass → re-synthesis → row UPGRADED, not orphaned."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_retrieval(monkeypatch, answer_mod)

    hedged = _Provider("The provided excerpts do not contain the implementation details.")
    _patch_provider(monkeypatch, answer_mod, hedged)
    first = await get_answer(QUESTION)
    assert first["confidence"] == "low"

    rows = await _cache_rows(factory)
    assert len(rows) == 1
    assert "do not contain" in _json.loads(rows[0].payload_json)["answer"]

    direct = _Provider("Auth flows through src/auth/service.py via AuthService.check().")
    _patch_provider(monkeypatch, answer_mod, direct)
    second = await get_answer(QUESTION)
    assert direct.calls == 1, "hedged cache entry must be bypassed, not returned"
    assert "AuthService.check" in second["answer"]

    rows = await _cache_rows(factory)
    assert len(rows) == 1, "upsert must replace the hedged row, not duplicate or fail"
    upgraded = _json.loads(rows[0].payload_json)
    assert "AuthService.check" in upgraded["answer"], "row must carry the upgraded answer"

    # Third call: the upgraded row is a normal cache hit — no synthesis.
    third = await get_answer(QUESTION)
    assert direct.calls == 1, "upgraded row must serve from cache"
    assert third["_meta"].get("cached") is True
    assert "_indexed_commit" not in third


@pytest.mark.asyncio
async def test_cached_empty_answer_row_is_bypassed(setup_mcp, factory, session, monkeypatch):
    """A legacy cached gated payload (empty answer) never serves from cache.

    Older versions cached the gated best_guesses payload, pinning a retrieval
    miss until TTL and hiding every later improvement to the miss path. The
    write side no longer stores empty answers; the read side must retire the
    rows that predate that fix.
    """
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer
    from repowise.server.mcp_server.tool_answer.answer import _hash_question
    from repowise.server.mcp_server.tool_answer.config import _ANSWER_SCHEMA_VERSION

    _patch_retrieval(monkeypatch, answer_mod)

    res = await session.execute(select(Repository))
    repo = res.scalars().first()
    # Legacy row: current schema version (so the schema gate passes) but the
    # old empty-answer gated shape.
    session.add(AnswerCache(
        repository_id=repo.id,
        question_hash=_hash_question(QUESTION),
        question=QUESTION,
        payload_json=_json.dumps({
            "answer": "",
            "confidence": "low",
            "fallback_targets": ["src/auth/service.py"],
            "_schema_version": _ANSWER_SCHEMA_VERSION,
        }),
        provider_name="mock",
        model_name="mock-1",
    ))
    await session.commit()

    direct = _Provider("Auth flows through src/auth/service.py via AuthService.check().")
    _patch_provider(monkeypatch, answer_mod, direct)
    result = await get_answer(QUESTION)
    assert direct.calls == 1, "empty-answer cache row must be bypassed"
    assert "AuthService.check" in result["answer"]


@pytest.mark.asyncio
async def test_cache_bypassed_when_indexed_commit_changes(setup_mcp, factory, session, monkeypatch):
    """A row stamped at commit A is bypassed once the repo is indexed at B."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_retrieval(monkeypatch, answer_mod)

    repo = (await session.execute(select(Repository))).scalars().first()
    repo.head_commit = "a" * 40
    await session.commit()

    v1 = _Provider("Answer synthesised at commit A (src/auth/service.py).")
    _patch_provider(monkeypatch, answer_mod, v1)
    await get_answer(QUESTION)
    assert v1.calls == 1

    # Same commit → cache hit, no new synthesis.
    await get_answer(QUESTION)
    assert v1.calls == 1

    repo.head_commit = "b" * 40
    await session.commit()

    v2 = _Provider("Answer synthesised at commit B (src/auth/service.py).")
    _patch_provider(monkeypatch, answer_mod, v2)
    result = await get_answer(QUESTION)
    assert v2.calls == 1, "commit change must bypass the cached row"
    assert "commit B" in result["answer"]

    rows = await _cache_rows(factory)
    assert len(rows) == 1
    assert _json.loads(rows[0].payload_json)["_indexed_commit"] == "b" * 40


@pytest.mark.asyncio
async def test_cache_row_past_ttl_is_bypassed(setup_mcp, factory, session, monkeypatch):
    """Rows older than the hard TTL re-synthesise even without commit metadata."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_retrieval(monkeypatch, answer_mod)

    v1 = _Provider("Original answer (src/auth/service.py).")
    _patch_provider(monkeypatch, answer_mod, v1)
    await get_answer(QUESTION)

    rows = await _cache_rows(factory)
    assert len(rows) == 1
    from repowise.core.persistence.database import get_session

    async with get_session(factory) as s:
        row = (await s.execute(select(AnswerCache))).scalars().one()
        row.created_at = datetime.now(UTC) - timedelta(days=30)
        await s.commit()

    v2 = _Provider("Fresh answer (src/auth/service.py).")
    _patch_provider(monkeypatch, answer_mod, v2)
    result = await get_answer(QUESTION)
    assert v2.calls == 1, "expired row must be bypassed"
    assert "Fresh answer" in result["answer"]


@pytest.mark.asyncio
async def test_cache_write_failure_logs_instead_of_silencing(setup_mcp, monkeypatch, caplog):
    """A failing cache write must not block the response — and must be logged."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_retrieval(monkeypatch, answer_mod)
    _patch_provider(monkeypatch, answer_mod, _Provider("Answer text (src/auth/service.py)."))

    real_dumps = answer_mod._json.dumps

    def _boom(obj, *a, **k):
        if isinstance(obj, dict) and "_schema_version" in obj:
            raise RuntimeError("simulated serialization failure")
        return real_dumps(obj, *a, **k)

    monkeypatch.setattr(answer_mod._json, "dumps", _boom)

    with caplog.at_level("WARNING", logger="repowise.mcp.answer"):
        result = await get_answer(QUESTION)

    assert result["answer"], "response must survive a cache-write failure"
    assert any("cache write failed" in r.message for r in caplog.records)
