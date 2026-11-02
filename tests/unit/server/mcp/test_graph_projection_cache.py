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
async def test_concurrent_cold_questions_build_once(session, repo_id):
    calls: list[int] = []
    build = _counting_build(calls)

    await asyncio.gather(*(per_index(session, repo_id, "adjacency", build) for _ in range(3)))

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
