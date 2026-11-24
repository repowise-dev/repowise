"""``embed.embedded_context``, ``embed.release`` and ``embed.set_repo_cap``."""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repowise.core.persistence.crud.analysis import fix_first
from repowise.core.persistence.database import create_engine, init_db
from repowise.core.persistence.index_cache import IndexStampCache
from repowise.core.persistence.models import Page, Repository
from repowise.core.workspace.config import RepoEntry, WorkspaceConfig
from repowise.core.workspace.registry import REPOSITORY_ID_INFO_KEY, RepoRegistry
from repowise.server.mcp_server import _basis, _components, _graph_files, embed
from repowise.server.mcp_server.tool_overview import graph as overview_graph

_NOW = datetime(2026, 11, 24, 10, 0, 0, tzinfo=UTC)
_ANIMAL = {"a": "heron", "b": "ocelot"}


@pytest.fixture
def fresh_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty caches and shipped limits, both restored after the test."""
    monkeypatch.setattr(_graph_files, "_CACHE", OrderedDict())
    monkeypatch.setattr(_basis, "_CACHE", {})
    monkeypatch.setattr(_components, "_CACHE", {})
    monkeypatch.setattr(overview_graph, "_DEPENDENCY_CACHE", {})
    monkeypatch.setattr(fix_first, "_cache", OrderedDict())
    for module, name in (
        (_graph_files, "_CACHE_MAX_REPOS"),
        (_basis, "_CACHE_MAX"),
        (_components, "_CACHE_LIMIT"),
        (overview_graph, "_DEPENDENCY_CACHE_LIMIT"),
    ):
        monkeypatch.setattr(module, name, getattr(module, name))


def _fill(repo_ids: list[str]) -> IndexStampCache:
    stamped = IndexStampCache(16)
    for rid in repo_ids:
        _graph_files._CACHE[rid] = ("state", {"projection": rid})
        _basis._CACHE[(rid, "state")] = {}
        _components._CACHE[(rid, "state", "/root", (), None, 0, 0)] = rid
        overview_graph._DEPENDENCY_CACHE[(rid, "state", 0)] = {}
        fix_first._cache[("sqlite://", rid, None, ("stamp",), True, None, None)] = rid
        stamped.remember(("sqlite://", rid, ("stamp",)), rid)
    return stamped


def _repos_held(stamped: IndexStampCache) -> list[set[str]]:
    return [
        set(_graph_files._CACHE),
        {k[0] for k in _basis._CACHE},
        {k[0] for k in _components._CACHE},
        {k[0] for k in overview_graph._DEPENDENCY_CACHE},
        {k[1] for k in fix_first._cache},
        {k[1] for k in stamped._entries},
    ]


def test_release_drops_only_that_repository(fresh_caches: None) -> None:
    stamped = _fill(["a", "b"])
    embed.release("a")
    assert _repos_held(stamped) == [{"b"}] * 6
    embed.release("missing")  # nothing held: a no-op
    assert _repos_held(stamped) == [{"b"}] * 6


def test_limits_stay_as_shipped_without_a_cap() -> None:
    assert _graph_files._CACHE_MAX_REPOS == 8
    assert overview_graph._DEPENDENCY_CACHE_LIMIT == 16
    assert _components._CACHE_LIMIT == 16
    assert _basis._CACHE_MAX == 32


def test_cap_trims_each_cache_in_its_own_style(fresh_caches: None) -> None:
    _fill(["a", "b", "c"])
    _graph_files._cached("a", "state", "projection")  # "a" is now the most recent
    embed.set_repo_cap(2)
    # Least recently used goes first.
    assert list(_graph_files._CACHE) == ["c", "a"]
    # Oldest inserted goes first.
    assert [k[0] for k in _basis._CACHE] == ["b", "c"]
    # A cache that clears when full clears when over the cap.
    assert _components._CACHE == {}
    assert overview_graph._DEPENDENCY_CACHE == {}
    assert (
        _graph_files._CACHE_MAX_REPOS
        == _basis._CACHE_MAX
        == _components._CACHE_LIMIT
        == overview_graph._DEPENDENCY_CACHE_LIMIT
        == 2
    )


def test_cap_must_hold_a_repository() -> None:
    with pytest.raises(ValueError):
        embed.set_repo_cap(0)


@pytest.mark.asyncio
async def test_cap_bounds_later_inserts(fresh_caches: None) -> None:
    embed.set_repo_cap(1)

    class _Session:
        async def execute(self, _stmt):
            return self

        def scalar_one_or_none(self):
            return None

    async def build() -> str:
        return "value"

    for rid in ("a", "b"):
        await _graph_files.per_index(_Session(), rid, "projection", build)
    assert list(_graph_files._CACHE) == ["b"]


def _seed(db: Path, roots: dict[str, Path]) -> None:
    async def run() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{db.as_posix()}")
        await init_db(engine)
        async with async_sessionmaker(engine, class_=AsyncSession)() as session:
            for name, root in roots.items():
                session.add(
                    Repository(
                        id=f"repo-{name}",
                        name=name,
                        url=f"https://example.com/{name}",
                        local_path=str(root),
                        default_branch="main",
                        settings_json="{}",
                        created_at=_NOW,
                        updated_at=_NOW,
                    )
                )
                await session.flush()  # the page's repository row first
                session.add(
                    Page(
                        id=f"repo_overview:{name}",
                        repository_id=f"repo-{name}",
                        page_type="repo_overview",
                        title=f"{name} overview",
                        content=f"# {name}\n\nThe zebrafish service of {name}.",
                        target_path=name,
                        source_hash="abc",
                        model_name="mock",
                        provider_name="mock",
                        generation_level=6,
                        confidence=1.0,
                        freshness_status="fresh",
                        metadata_json="{}",
                        created_at=_NOW,
                        updated_at=_NOW,
                    )
                )
            await session.commit()
        await engine.dispose()

    asyncio.run(run())


@pytest.mark.asyncio
async def test_embedded_context_matches_a_registered_repository(tmp_path: Path) -> None:
    roots = {name: (tmp_path / name).resolve() for name in ("a", "b")}
    for root in roots.values():
        root.mkdir()
    db = tmp_path / "shared.db"
    # Seeded on its own loop, before this test's loop opens the store.
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        pool.submit(_seed, db, roots).result()

    config = WorkspaceConfig(
        version=1,
        repos=[RepoEntry(path="a", alias="a", is_primary=True), RepoEntry(path="b", alias="b")],
        default_repo="a",
    )
    registry = RepoRegistry(tmp_path, config, db_url=f"sqlite+aiosqlite:///{db.as_posix()}")
    try:
        served = await registry.get("b")
        embedded = embed.embedded_context(registry._shared_engine, "repo-b", alias="b")
        for name in roots:
            await served.fts.index(
                f"repo_overview:{name}",
                f"{name} overview",
                f"The {_ANIMAL[name]} service routes requests, stores records and "
                "reports usage for every tenant of the platform each night.",
                summary=f"{_ANIMAL[name]} service",
                target_path=name,
            )

        assert embedded.alias == served.alias
        assert embedded.session_factory.kw == served.session_factory.kw
        assert embedded.session_factory.kw["info"] == {REPOSITORY_ID_INFO_KEY: "repo-b"}
        assert embedded.fts._repository_id == served.fts._repository_id == "repo-b"
        for word, expected in (("ocelot", ["repo_overview:b"]), ("heron", [])):
            hits = [h.page_id for h in await embedded.fts.search(word)]
            assert hits == [h.page_id for h in await served.fts.search(word)] == expected
        # No checkout: no path, no source reader, no git, nothing to wait on.
        assert (embedded.path, embedded.source, embedded.git) == (None, None, False)
        assert embedded.vector_store_ready is None
        assert embedded.vector_store is embedded.decision_store is None
        assert embedded._engine is None  # the caller owns and disposes the engine
    finally:
        await registry.close()
