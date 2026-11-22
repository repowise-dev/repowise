"""The idle warmup builds what a first question would, and only that."""

from __future__ import annotations

import asyncio

import pytest

from repowise.server.mcp_server import _graph_files, _warmup


@pytest.fixture
def cold_caches():
    _graph_files.reset_cache()
    yield
    _graph_files.reset_cache()


def _cached_names(repo_id: str) -> set[str]:
    entry = _graph_files._CACHE.get(repo_id)
    return set(entry[1]) if entry else set()


@pytest.mark.asyncio
async def test_warmup_builds_every_cache_its_steps_name(setup_mcp, cold_caches):
    await _warmup.warm_index_caches()

    assert _cached_names(setup_mcp) == {name for name, _ in _warmup.WARM_STEPS}


@pytest.mark.asyncio
async def test_first_questions_after_a_warmup_build_nothing_new(setup_mcp, cold_caches):
    """A per-index cache a question reads but the warmup skips fails here.

    That is how a new or renamed cache gets added to ``WARM_STEPS``.
    """
    from repowise.server.mcp_server import get_answer, search_codebase

    await _warmup.warm_index_caches()
    warmed = _cached_names(setup_mcp)

    await get_answer("How does authentication work?")
    await search_codebase("authentication", mode="concept")
    await search_codebase("calculate_total")

    assert _cached_names(setup_mcp) - warmed == set()


@pytest.mark.asyncio
async def test_warmup_is_skipped_when_free_memory_is_short(monkeypatch):
    monkeypatch.setattr("repowise.core.procutils.available_memory_bytes", lambda: 1)
    _warmup.start()
    try:
        assert _warmup._task is None
    finally:
        await _warmup.stop()


@pytest.mark.asyncio
async def test_a_large_store_asks_for_more_free_memory(monkeypatch, tmp_path):
    from repowise.server.mcp_server import _state

    store = tmp_path / ".repowise" / "wiki.db"
    store.parent.mkdir()
    store.write_bytes(b"\0" * 1024)
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))
    monkeypatch.setattr(_warmup, "MIN_FREE_MEMORY_BYTES", 1024)

    assert _warmup._memory_needed() == 2048


@pytest.mark.asyncio
async def test_stop_cancels_a_warmup_in_flight(monkeypatch):
    started = asyncio.Event()

    async def _slow() -> None:
        started.set()
        await asyncio.sleep(60)

    monkeypatch.setattr(_warmup, "warm_index_caches", _slow)
    monkeypatch.setattr("repowise.core.procutils.available_memory_bytes", lambda: None)
    _warmup.start()
    task = _warmup._task
    await started.wait()
    await _warmup.stop()

    assert task is not None and task.cancelled()
    assert _warmup._task is None


@pytest.mark.asyncio
async def test_a_session_starts_the_warmup_once_initialized(tmp_path, monkeypatch):
    from tests.unit.server.mcp.test_client_profiles import _session

    calls: list[int] = []
    monkeypatch.setattr(_warmup, "start", lambda: calls.append(1))
    await _session(tmp_path, monkeypatch, "claude-code", None)

    assert calls
