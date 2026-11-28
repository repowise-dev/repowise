"""``get_change_risk``'s ``measured`` block: present only when the change edits a measured function."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repowise.core.persistence.crud import replace_workload_hotness
from repowise.server.mcp_server.tool_change_risk.measured import measured_block
from tests.unit.persistence.helpers import make_wiki_symbol
from tests.unit.persistence.test_measured_costs import _hot, _run
from tests.unit.server.mcp.test_change_risk import _commit, _factory_with_repo, _git

APP = "src/app.py"
SEED = "def serve():\n    a = 1\n    return a\n\n\ndef helper():\n    b = 2\n    return b\n"


async def _measured_index(engine, *, hot: bool = True):
    factory = await _factory_with_repo(engine, None)
    async with factory() as s:
        s.add_all(
            [
                make_wiki_symbol("repo1", APP, "serve", start_line=1, end_line=3),
                make_wiki_symbol("repo1", APP, "helper", start_line=6, end_line=8),
                _run("repo1", "r1"),
            ]
        )
        await s.flush()
        if hot:
            await replace_workload_hotness(s, "repo1", [_hot("serve")])
        await s.commit()
    return factory


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, {APP: SEED}, "chore: seed")
    return repo


async def _change_risk(monkeypatch, repo, factory) -> dict:
    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo), session_factory=factory)

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    return await module.get_change_risk("HEAD", baseline=0)


@pytest.mark.asyncio
async def test_a_change_to_a_measured_function_names_it_and_its_rerun(
    engine, tmp_path, monkeypatch
) -> None:
    repo = _repo(tmp_path)
    _commit(repo, {APP: SEED.replace("a = 1", "a = 2")}, "perf: serve")
    factory = await _measured_index(engine)

    result = await _change_risk(monkeypatch, repo, factory)

    assert result["measured"]["functions"] == [
        {
            "fn": f"{APP}::serve",
            "measured": 'rank 1 in entry_point "w-r1"',
            "verify": "repowise perf measure --save before -- w-r1 before the change, "
            "then --against before after it",
        }
    ]


@pytest.mark.asyncio
async def test_a_change_beside_a_measured_function_adds_no_key(
    engine, tmp_path, monkeypatch
) -> None:
    repo = _repo(tmp_path)
    # ``helper`` shares the file with the measured ``serve``.
    _commit(repo, {APP: SEED.replace("b = 2", "b = 3")}, "fix: helper")
    factory = await _measured_index(engine)

    result = await _change_risk(monkeypatch, repo, factory)

    assert "measured" not in result


@pytest.mark.asyncio
async def test_the_block_is_silent_without_an_index_or_a_repository(engine) -> None:
    assert await measured_block(None, {APP: {2}}) is None
    empty = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    # No repository row: the lookup fails, and the block stays absent.
    assert await measured_block(empty, {APP: {2}}) is None


@pytest.mark.asyncio
async def test_an_evidence_ingest_moves_the_range_cache_key(engine, tmp_path) -> None:
    from repowise.server.mcp_server.tool_change_risk import cache

    repo = _repo(tmp_path)
    _commit(repo, {APP: SEED.replace("a = 1", "a = 2")}, "perf: serve")
    factory = await _measured_index(engine, hot=False)
    ctx = SimpleNamespace(path=str(repo), session_factory=factory)
    before = await cache.cache_key(ctx, "HEAD", None, None, (), 0)

    async with factory() as s:
        s.add(_run("repo1", "r2"))
        await s.commit()

    assert await cache.cache_key(ctx, "HEAD", None, None, (), 0) != before
