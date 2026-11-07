"""Incremental store reads follow the configured database, not the local file.

Four loaders decided a store existed by looking for ``<repo>/.repowise/wiki.db``
and then opened ``resolve_db_url()``, which prefers ``REPOWISE_DB_URL``. Under a
shared database that file is absent by design, so every incremental run took
the "no store" path. The same predicate as the head-commit stamper has to gate
all four, and it still must not conjure a database when nothing is configured.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.pipeline.incremental import (
    load_stored_coverage_map,
    load_stored_function_mod_p80,
    load_stored_git_meta,
    load_stored_performance_callers,
)


def _clear_db_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("REPOWISE_DB_URL", "REPOWISE_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)


def _configure_shared_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point ``REPOWISE_DB_URL`` at a sqlite file outside the repo."""
    shared = tmp_path / "shared" / "index.db"
    shared.parent.mkdir(parents=True, exist_ok=True)
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite:///{shared.as_posix()}")
    return shared


async def _read(name: str, repo: Path):
    if name == "git_meta":
        return await load_stored_git_meta(repo)
    if name == "performance_callers":
        return await load_stored_performance_callers(repo, {"src/a.py"})
    if name == "function_mod_p80":
        return await load_stored_function_mod_p80(repo)
    return await load_stored_coverage_map(repo)


_EMPTY = {
    "git_meta": None,
    "performance_callers": None,
    "function_mod_p80": None,
    "coverage_map": {},
}


@pytest.mark.parametrize("name", list(_EMPTY))
async def test_a_configured_database_is_opened(tmp_path, monkeypatch, name):
    """Opening the store creates the file; a guard that returns early leaves it absent."""
    shared = _configure_shared_db(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    assert not shared.exists()
    assert not (repo / ".repowise" / "wiki.db").exists()

    await _read(name, repo)

    assert shared.exists(), "the loader returned before it ever opened the store"
    assert not (repo / ".repowise" / "wiki.db").exists()


@pytest.mark.parametrize("name", list(_EMPTY))
async def test_loaders_still_noop_when_nothing_is_configured(tmp_path, monkeypatch, name):
    """No configured URL and no local file: the empty value, and no database created."""
    _clear_db_env(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()

    assert await _read(name, repo) == _EMPTY[name]
    assert not (repo / ".repowise" / "wiki.db").exists()
    assert not (repo / ".repowise").exists()
