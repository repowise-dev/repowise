"""Hybrid search does not hold full-text hits for a slow vector leg.

The vector leg waits on an embedding round trip: ~0.3s warm, 5-13s on a cold
store or a rate-limited embedder, while full-text answers in tens of
milliseconds. Once the other legs are in, the vector leg gets a short grace;
past it the reply goes out without semantic hits and says so in ``_meta``.
"""

from __future__ import annotations

import asyncio

import pytest

from repowise.server.mcp_server import tool_search
from repowise.server.mcp_server._answer_pipeline.leg_record import (
    begin_leg_record,
    retrieval_legs,
    vector_leg_within,
)
from repowise.server.mcp_server._helpers import (
    _SEMANTIC_GRACE_DEFAULT_S,
    _SEMANTIC_GRACE_ENV,
    semantic_grace_s,
)
from repowise.server.mcp_server.tool_search.tool import _flag_degraded_legs


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(_SEMANTIC_GRACE_ENV, raising=False)


def test_grace_default_and_override(monkeypatch) -> None:
    assert semantic_grace_s() == _SEMANTIC_GRACE_DEFAULT_S
    monkeypatch.setenv(_SEMANTIC_GRACE_ENV, "2.5")
    assert semantic_grace_s() == 2.5
    monkeypatch.setenv(_SEMANTIC_GRACE_ENV, "0")
    assert semantic_grace_s() == _SEMANTIC_GRACE_DEFAULT_S


def _leg(delay: float, done: list[str]):
    async def leg():
        await asyncio.sleep(delay)
        done.append("vector")
        return ["hit"]

    return leg()


def test_a_leg_inside_the_grace_is_used() -> None:
    async def go():
        record = begin_leg_record()
        hits = await vector_leg_within(asyncio.ensure_future(_leg(0.01, [])), 1.0)
        return hits, record

    hits, record = asyncio.run(go())
    assert hits == ["hit"]
    assert record == {}


def test_a_late_leg_is_skipped_recorded_and_left_to_finish() -> None:
    done: list[str] = []

    async def go():
        record = begin_leg_record()
        hits = await vector_leg_within(asyncio.ensure_future(_leg(0.2, done)), 0.01)
        finished_before = list(done)
        await asyncio.sleep(0.3)  # the leg completes: its embedding gets cached
        return hits, record, finished_before

    hits, record, finished_before = asyncio.run(go())
    assert hits == []
    assert record == {"vector": "skipped"}
    assert finished_before == []
    assert done == ["vector"]


class _Ctx:
    def __init__(self) -> None:
        self.fts = None
        self.vector_store = None


def test_fused_retrieve_returns_full_text_without_waiting(monkeypatch) -> None:
    class _Hit:
        page_id = "file_page:a.py"
        title = "a.py"
        score = 1.0
        snippet = ""
        page_type = "file_page"
        target_path = "a.py"

    async def fts(ctx, query, limit):
        return [_Hit()]

    async def vector(ctx, query, limit):
        await asyncio.sleep(5)
        return []

    async def nothing(*args, **kwargs):
        return []

    monkeypatch.setenv(_SEMANTIC_GRACE_ENV, "0.05")
    monkeypatch.setattr(tool_search, "_safe_fts", fts)
    monkeypatch.setattr(tool_search, "_safe_vector", vector)
    monkeypatch.setattr(tool_search, "_safe_symbol_search", nothing)
    monkeypatch.setattr(tool_search, "_safe_filename_search", nothing)

    async def go():
        begin_leg_record()
        loop = asyncio.get_running_loop()
        start = loop.time()
        out = await tool_search._fused_retrieve(_Ctx(), "q", 5, None)
        elapsed = loop.time() - start
        reply = _flag_degraded_legs({"results": out, "_meta": {}})
        for task in asyncio.all_tasks() - {asyncio.current_task()}:
            task.cancel()
        return out, elapsed, reply, retrieval_legs()

    out, elapsed, reply, legs = asyncio.run(go())
    assert [r["page_id"] for r in out] == ["file_page:a.py"]
    assert elapsed < 1.0
    assert legs == {"vector": "skipped"}
    assert reply["_meta"]["retrieval_degraded"] == ["vector"]


def test_a_whole_reply_carries_no_flag() -> None:
    async def go():
        begin_leg_record()
        return _flag_degraded_legs({"results": [], "_meta": {}})

    assert asyncio.run(go()) == {"results": [], "_meta": {}}
