"""The idle warmup builds what a first question would, and only that."""

from __future__ import annotations

import asyncio

import pytest

from repowise.server.mcp_server import _graph_files, _warmup


@pytest.fixture
def cold_caches(monkeypatch):
    from repowise.server.mcp_server import _state

    # An earlier lifespan in this process may have served "no index".
    monkeypatch.setattr(_state, "_serving_no_index", False)
    monkeypatch.setattr(_warmup, "_call_seen", False)
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
    assert warmed

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
@pytest.mark.parametrize("value", ["false", "off"])
async def test_warmup_is_skipped_when_the_setting_is_off(monkeypatch, tmp_path, value):
    from repowise.server.mcp_server import _state

    config = tmp_path / ".repowise" / "config.yaml"
    config.parent.mkdir()
    config.write_text(f"performance:\n  warm_caches: {value}\n", encoding="utf-8")
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))
    monkeypatch.setattr("repowise.core.procutils.available_memory_bytes", lambda: None)
    _warmup.start()
    try:
        assert _warmup._task is None
    finally:
        await _warmup.stop()


@pytest.mark.parametrize(
    "text", [None, "performance:\n  warm_caches: true\n", "performance: [unreadable\n"]
)
def test_warmup_stays_on_unless_the_setting_says_off(monkeypatch, tmp_path, text):
    from repowise.server.mcp_server import _state

    if text is not None:
        config = tmp_path / ".repowise" / "config.yaml"
        config.parent.mkdir()
        config.write_text(text, encoding="utf-8")
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path))

    assert _warmup._turned_off() is False


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


@pytest.mark.asyncio
async def test_a_tool_call_stops_the_warmup_after_the_cache_in_flight(
    setup_mcp, cold_caches, monkeypatch
):
    built: list[str] = []

    def step(name: str, *, call: bool = False):
        async def run(_session, _repo_id):
            built.append(name)
            if call:
                _warmup.call_started()

        return name, run

    monkeypatch.setattr(_warmup, "WARM_STEPS", (step("a", call=True), step("b"), step("c")))
    await _warmup.warm_index_caches()

    assert built == ["a"]


@pytest.mark.asyncio
async def test_a_finished_warmup_says_so(setup_mcp, cold_caches, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="repowise.mcp"):
        await _warmup.warm_index_caches()

    assert any("index caches warm" in r.getMessage() for r in caplog.records)
