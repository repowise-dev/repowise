"""Tests for the reindex CLI command internals."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


def test_reindex_command_forwards_verbose_to_configure_cli_logging(monkeypatch, tmp_path: Path) -> None:
    """`--verbose` must reach configure_cli_logging before any pipeline work."""
    seen: dict[str, object] = {}

    def fake_configure(*, verbose: bool = False) -> None:
        seen["verbose"] = verbose

    def fake_resolve_repo_path(_path: str | None) -> Path:
        return tmp_path

    def fake_ensure_repowise_dir(_repo_path: Path) -> Path:
        return tmp_path / ".repowise"

    def fake_run_async(_coro) -> None:
        # Do not execute the coroutine body; just close it.
        _coro.close()

    monkeypatch.setattr(reindex_cmd, "configure_cli_logging", fake_configure)
    monkeypatch.setattr(reindex_cmd, "resolve_repo_path", fake_resolve_repo_path)
    monkeypatch.setattr(reindex_cmd, "ensure_repowise_dir", fake_ensure_repowise_dir)
    monkeypatch.setattr(reindex_cmd, "run_async", fake_run_async)
    monkeypatch.setattr("repowise.cli.ui.load_dotenv", lambda _repo_path: None)

    # Invoke the underlying function (not the Click wrapper) so we control kwargs.
    reindex_cmd.reindex_command.callback(
        path=None, embedder="mock", batch_size=32, verbose=True
    )
    assert seen.get("verbose") is True

    seen.clear()
    reindex_cmd.reindex_command.callback(
        path=None, embedder="mock", batch_size=32, verbose=False
    )
    assert seen.get("verbose") is False

