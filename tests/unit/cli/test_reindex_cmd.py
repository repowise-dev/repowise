"""Tests for the reindex CLI command internals."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
import pytest

from repowise.cli.commands import reindex_cmd


class _DummyEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class _EmptyResult:
    def scalars(self) -> _EmptyResult:
        return self

    def all(self) -> list[Any]:
        return []


class _Session:
    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, _stmt: object) -> _EmptyResult:
        return _EmptyResult()


def _sessionmaker(*_args: object, **_kwargs: object):
    return _Session


async def test_reindex_uses_shared_database_engine(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'wiki.db'}"
    created: dict[str, object] = {}

    def fake_create_engine(url: str):
        engine = _DummyEngine()
        created["url"] = url
        created["engine"] = engine
        return engine

    async def fake_init_db(engine: object) -> None:
        created["init_engine"] = engine

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(reindex_cmd, "get_db_url_for_repo", lambda _repo_path: db_url)
    monkeypatch.setattr(
        "repowise.core.persistence.database.create_engine",
        fake_create_engine,
    )
    monkeypatch.setattr("repowise.core.persistence.database.init_db", fake_init_db)
    monkeypatch.setattr("sqlalchemy.ext.asyncio.async_sessionmaker", _sessionmaker)

    await reindex_cmd._reindex(tmp_path, "openai", batch_size=20)

    assert created["url"] == db_url
    assert created["init_engine"] is created["engine"]
    assert created["engine"].disposed is True


async def test_reindex_aborts_when_every_item_failed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A reindex that indexed nothing but failed on every item must not exit 0.

    An automated pipeline (or an agent) treats a zero exit as a successful
    build, so an empty vector index after a total embedder failure would be
    mistaken for a healthy reindex.
    """
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'wiki.db'}"

    def fake_create_engine(url: str):
        return _DummyEngine()

    async def fake_init_db(engine: object) -> None:
        return None

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(reindex_cmd, "get_db_url_for_repo", lambda _repo_path: db_url)
    monkeypatch.setattr(
        "repowise.core.persistence.database.create_engine",
        fake_create_engine,
    )
    monkeypatch.setattr("repowise.core.persistence.database.init_db", fake_init_db)
    monkeypatch.setattr("sqlalchemy.ext.asyncio.async_sessionmaker", _sessionmaker)

    # No pages in the DB → nothing to index, nothing failed → no abort.
    await reindex_cmd._reindex(tmp_path, "openai", batch_size=20)

    # Force the failure path: every item fails to embed. Patch the vector
    # store's embed_batch to raise, and give the store a page to process.
    class _FailingStore:
        async def embed_batch(self, items: list[Any]) -> None:
            raise RuntimeError("embedder down")

        async def embed_and_upsert(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("embedder down")

        async def close(self) -> None:
            return None

    class _Page:
        id = "p1"
        title = "Page"
        page_type = "file_page"
        target_path = "src/main.py"
        summary = ""
        content = "body"
        decision = "a decision"

    class _FailingSession:
        async def __aenter__(self) -> _FailingSession:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def execute(self, _stmt: object) -> _EmptyResult:
            return _EmptyResult()

    class _FailingResult(_EmptyResult):
        def all(self) -> list[Any]:
            return [_Page()]

    class _FailingSession2:
        async def __aenter__(self) -> _FailingSession2:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def execute(self, _stmt: object) -> _FailingResult:
            return _FailingResult()

    def _failing_sessionmaker(*_args: object, **_kwargs: object):
        return _FailingSession2

    monkeypatch.setattr("sqlalchemy.ext.asyncio.async_sessionmaker", _failing_sessionmaker)
    monkeypatch.setattr(
        "repowise.core.persistence.vector_store.LanceDBVectorStore",
        lambda *a, **k: _FailingStore(),
    )

    with pytest.raises(click.Abort):
        await reindex_cmd._reindex(tmp_path, "openai", batch_size=20)


async def test_reindex_auto_honours_the_repo_pinned_embedder(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """`--embedder auto` must read config.yaml's pin, not just the environment.

    The pin is what wrote the vector table, so it is what must rewrite it. When
    `auto` resolved from env vars alone, a repo pinned to one embedder in a
    shell exporting another provider's key had its table rewritten by that other
    provider, leaving every reader — which does honour the pin — querying
    vectors of the wrong width.
    """
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'wiki.db'}"
    (tmp_path / ".repowise").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "embedder: ollama\n", encoding="utf-8"
    )

    built: dict[str, str] = {}

    def fake_build_embedder(name: str, _repo_path: object):
        built["name"] = name
        raise click.Abort()

    # REPOWISE_EMBEDDER legitimately outranks the pin, so a test about pin
    # precedence has to clear it or it is asserting the wrong rule. It is also
    # genuinely leaked into this process by
    # test_embedder_resolution.py::test_the_reader_prefers_the_repo_pin, whose
    # `monkeypatch.delenv(..., raising=False)` records nothing when the variable
    # is already absent — so the value the production code then sets has no
    # saved state to restore and survives into later tests.
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)
    # An unrelated provider key exported in the environment. Before the fix this
    # is what `auto` resolved to, silently overriding the repo's own pin.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(reindex_cmd, "get_db_url_for_repo", lambda _repo_path: db_url)
    monkeypatch.setattr(
        "repowise.cli.providers.embedders.build_embedder", fake_build_embedder
    )

    with pytest.raises(click.Abort):
        await reindex_cmd._reindex(tmp_path, "auto", batch_size=20)

    assert built["name"] == "ollama"


async def test_reindex_does_not_abort_on_a_deliberately_pinned_mock_embedder(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """`embedder: mock` in config.yaml is a choice, not a missing embedder.

    The abort exists for the fallback case — nothing configured, so build_embedder
    quietly hands back a MockEmbedder and a real reindex would write meaningless
    vectors. A repo that pins mock has said what it wants, the same as passing
    --embedder mock, and must not be treated as unconfigured.
    """
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'wiki.db'}"
    (tmp_path / ".repowise").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "embedder: mock\n", encoding="utf-8"
    )

    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)
    monkeypatch.setattr(reindex_cmd, "get_db_url_for_repo", lambda _repo_path: db_url)
    monkeypatch.setattr(
        "repowise.core.persistence.database.create_engine", lambda _url: _DummyEngine()
    )

    async def fake_init_db(_engine: object) -> None:
        return None

    monkeypatch.setattr("repowise.core.persistence.database.init_db", fake_init_db)
    monkeypatch.setattr("sqlalchemy.ext.asyncio.async_sessionmaker", _sessionmaker)

    # No Abort: reaches the normal empty-database path and returns.
    await reindex_cmd._reindex(tmp_path, "auto", batch_size=20)
