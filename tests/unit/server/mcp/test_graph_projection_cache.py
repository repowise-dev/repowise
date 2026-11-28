"""A graph projection is built once per index state, not once per question."""

from __future__ import annotations

import asyncio
import os

import pytest

from repowise.server.mcp_server import _graph_files
from repowise.server.mcp_server._graph_files import per_index


def _counting_build(calls: list[int]):
    async def build():
        calls.append(1)
        await asyncio.sleep(0)
        return {"built": len(calls)}

    return build


@pytest.mark.asyncio
async def test_a_second_question_on_the_same_index_reuses_the_projection(session, repo_id):
    calls: list[int] = []
    build = _counting_build(calls)

    first = await per_index(session, repo_id, "adjacency", build)
    second = await per_index(session, repo_id, "adjacency", build)

    assert calls == [1]
    assert second is first


@pytest.mark.asyncio
async def test_concurrent_cold_questions_build_once(factory, repo_id):
    calls: list[int] = []
    build = _counting_build(calls)

    async def ask():
        # One session per question, as concurrent tool calls each open their own.
        async with factory() as s:
            return await per_index(s, repo_id, "adjacency", build)

    await asyncio.gather(*(ask() for _ in range(3)))

    assert calls == [1]


@pytest.mark.asyncio
async def test_the_end_of_an_update_run_invalidates_the_projection(session, repo_id, tmp_path):
    # The repo row is stamped when a run starts; the state file is saved when it
    # ends, so a projection read mid-run is not served after it.
    calls: list[int] = []
    build = _counting_build(calls)
    await per_index(session, repo_id, "adjacency", build)

    state = tmp_path / ".repowise" / "state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{}", encoding="utf-8")
    os.utime(state, ns=(1, 1))
    rebuilt = await per_index(session, repo_id, "adjacency", build)

    assert calls == [1, 1]
    assert rebuilt == {"built": 2}


@pytest.mark.asyncio
async def test_each_projection_is_cached_under_its_own_name(session, repo_id):
    calls: list[int] = []
    build = _counting_build(calls)

    await per_index(session, repo_id, "adjacency", build)
    await per_index(session, repo_id, "projected_edges", build)

    assert calls == [1, 1]


@pytest.mark.asyncio
async def test_the_least_recently_used_repo_is_evicted(session, repo_id, monkeypatch):
    monkeypatch.setattr(_graph_files, "_CACHE_MAX_REPOS", 1)
    calls: list[int] = []
    build = _counting_build(calls)

    await per_index(session, repo_id, "adjacency", build)
    await per_index(session, "another-repo", "adjacency", build)
    await per_index(session, repo_id, "adjacency", build)

    assert calls == [1, 1, 1]


@pytest.mark.asyncio
async def test_a_build_does_not_wait_on_another_projections_build(factory, repo_id):
    release = asyncio.Event()

    async def slow():
        await release.wait()
        return "slow"

    async def fast():
        return "fast"

    async def ask(name, build):
        async with factory() as s:
            return await per_index(s, repo_id, name, build)

    pending = asyncio.ensure_future(ask("slow", slow))
    await asyncio.sleep(0.05)
    try:
        assert await asyncio.wait_for(ask("fast", fast), timeout=5) == "fast"
    finally:
        release.set()
        assert await pending == "slow"


@pytest.fixture
def index_dir(tmp_path, monkeypatch):
    """A served index with a ``.repowise/`` and a seal key of the test's own."""
    from repowise.core import cache_seal

    monkeypatch.setenv("REPOWISE_CACHE_HMAC_KEY", "ab" * 32)
    cache_seal.reset_key_cache_for_tests()
    (tmp_path / ".repowise").mkdir()
    yield tmp_path
    cache_seal.reset_key_cache_for_tests()


async def _stored(index_dir):
    """Wait for the sealed copies in flight, then list them."""
    await asyncio.gather(*list(_graph_files._PENDING_STORES))
    return sorted(p.name for p in (index_dir / ".repowise" / "mcp_cache").iterdir())


@pytest.mark.asyncio
async def test_a_new_server_process_loads_the_projection_instead_of_building(
    session, repo_id, index_dir
):
    calls: list[int] = []
    build = _counting_build(calls)
    first = await per_index(session, repo_id, "adjacency", build, persist=True)
    assert await _stored(index_dir) == [f"{repo_id}.adjacency"]

    _graph_files.reset_cache()  # what a new process starts with
    loaded = await per_index(session, repo_id, "adjacency", build, persist=True)

    assert calls == [1]
    assert loaded == first


@pytest.mark.asyncio
async def test_a_stored_projection_from_another_index_state_is_rebuilt(session, repo_id, index_dir):
    calls: list[int] = []
    build = _counting_build(calls)
    await per_index(session, repo_id, "adjacency", build, persist=True)
    await _stored(index_dir)

    state = index_dir / ".repowise" / "state.json"
    state.write_text("{}", encoding="utf-8")
    os.utime(state, ns=(1, 1))
    _graph_files.reset_cache()
    rebuilt = await per_index(session, repo_id, "adjacency", build, persist=True)

    assert calls == [1, 1]
    assert rebuilt == {"built": 2}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("attr", "other"), [("_STORE_SCHEMA", 999), ("__version__", "0.0.0-older")]
)
async def test_a_copy_written_by_another_release_is_rebuilt(
    session, repo_id, index_dir, monkeypatch, attr, other
):
    import repowise.server as server_pkg

    calls: list[int] = []
    build = _counting_build(calls)
    await per_index(session, repo_id, "adjacency", build, persist=True)
    await _stored(index_dir)

    monkeypatch.setattr(_graph_files if attr == "_STORE_SCHEMA" else server_pkg, attr, other)
    _graph_files.reset_cache()
    rebuilt = await per_index(session, repo_id, "adjacency", build, persist=True)

    assert calls == [1, 1]
    assert rebuilt == {"built": 2}
    # The rebuilt copy replaced the old one rather than sitting beside it.
    assert await _stored(index_dir) == [f"{repo_id}.adjacency"]


@pytest.mark.asyncio
async def test_a_copy_is_never_read_for_another_repo(session, repo_id, index_dir):
    await per_index(session, repo_id, "adjacency", _counting_build([]), persist=True)
    await _stored(index_dir)
    store = index_dir / ".repowise" / "mcp_cache"
    # A copy renamed to another repo's slot fails its seal.
    (store / f"{repo_id}.adjacency").rename(store / "other.adjacency")
    path = store / "other.adjacency"

    assert _graph_files._load_stored(path, "other", "adjacency", "any") is _graph_files._MISS


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"not a sealed pickle", b"", "truncated"])
async def test_an_unsealed_corrupt_or_partial_copy_is_rebuilt(session, repo_id, index_dir, content):
    calls: list[int] = []
    build = _counting_build(calls)
    path = index_dir / ".repowise" / "mcp_cache" / f"{repo_id}.adjacency"
    builds_before = 0
    if content == "truncated":
        builds_before = 1
        await per_index(session, repo_id, "adjacency", build, persist=True)
        await _stored(index_dir)
        _graph_files.reset_cache()
        content = path.read_bytes()[:-5]
    else:
        path.parent.mkdir()
    path.write_bytes(content)

    value = await per_index(session, repo_id, "adjacency", build, persist=True)

    assert len(calls) == builds_before + 1
    assert value == {"built": len(calls)}


def test_two_servers_writing_and_reading_one_copy_never_fail(index_dir):
    import threading

    path = index_dir / ".repowise" / "mcp_cache" / "repo1.adjacency"
    value = {f"f{i}.py": [f"g{i}.py"] for i in range(5000)}
    errors: list[BaseException] = []
    seen: list[object] = []

    def writer():
        try:
            for _ in range(15):
                _graph_files._store(path, "repo1", "adjacency", "k", value)
        except BaseException as exc:
            errors.append(exc)

    def reader():
        try:
            for _ in range(30):
                seen.append(_graph_files._load_stored(path, "repo1", "adjacency", "k"))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=f) for f in (writer, writer, reader, reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    # Each read is the whole value or a miss, never a partial one.
    assert all(v is _graph_files._MISS or v == value for v in seen)
    assert _graph_files._load_stored(path, "repo1", "adjacency", "k") == value
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_temp_files_a_killed_write_left_are_removed(index_dir):
    path = index_dir / ".repowise" / "mcp_cache" / "repo1.adjacency"
    path.parent.mkdir()
    orphan = path.parent / f"{path.name}0123456789abcdef.tmp"
    orphan.write_bytes(b"x")
    os.utime(orphan, (1, 1))

    _graph_files._store(path, "repo1", "adjacency", "k", {"a": 1})

    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


@pytest.mark.asyncio
async def test_a_value_not_marked_persistent_stays_in_memory(session, repo_id, index_dir):
    await per_index(session, repo_id, "owners.data", _counting_build([]))
    await asyncio.gather(*list(_graph_files._PENDING_STORES))

    assert not (index_dir / ".repowise" / "mcp_cache").exists()


@pytest.mark.asyncio
async def test_no_copy_is_written_without_an_index_directory(session, repo_id, tmp_path):
    await per_index(session, repo_id, "adjacency", _counting_build([]), persist=True)
    await asyncio.gather(*list(_graph_files._PENDING_STORES))

    assert not (tmp_path / ".repowise").exists()
