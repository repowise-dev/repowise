"""``repowise update`` in a copied checkout must not duplicate the repository row."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from repowise.cli.helpers import save_state
from repowise.cli.main import cli
from repowise.core.ingestion.git_indexer import GIT_HISTORY_VERSION, GIT_HISTORY_VERSION_KEY
from repowise.core.persistence import create_engine, create_session_factory, get_session
from repowise.core.persistence.database import resolve_db_url
from repowise.core.persistence.models import Repository
from repowise.core.pipeline.full_index import index_repo_full
from repowise.server.mcp_server._meta import served_root

CURRENT_GIT_HISTORY = {GIT_HISTORY_VERSION_KEY: GIT_HISTORY_VERSION}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True
    ).stdout.strip()


def _seed_repo(tmp_path: Path) -> Path:
    orig = tmp_path / "orig"
    orig.mkdir()
    _git(orig, "init")
    _git(orig, "config", "user.email", "t@t.com")
    _git(orig, "config", "user.name", "T")
    (orig / "app").mkdir()
    (orig / "app" / "main.py").write_text("print('orig')\n", encoding="utf-8")
    _git(orig, "add", ".")
    _git(orig, "commit", "-m", "init")
    asyncio.run(index_repo_full(orig))
    head = _git(orig, "rev-parse", "HEAD")
    save_state(
        orig,
        {
            "last_sync_commit": head,
            "last_docs_commit": head,
            "docs_enabled": False,
            **CURRENT_GIT_HISTORY,
        },
    )
    return orig


@pytest.fixture
def db_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")


def test_update_in_copied_checkout_keeps_one_repository_row(
    tmp_path: Path, db_env: None
) -> None:
    orig = _seed_repo(tmp_path)
    moved = tmp_path / "moved"
    shutil.copytree(orig, moved)
    (moved / "app" / "main.py").write_text("print('moved')\n  # moved\n", encoding="utf-8")
    _git(moved, "add", "app/main.py")
    _git(moved, "commit", "-m", "moved marker")

    result = CliRunner().invoke(cli, ["update", str(moved), "--no-workspace"])
    assert result.exit_code == 0, result.output

    async def _read_rows() -> list[tuple[str, str]]:
        engine = create_engine(resolve_db_url(moved))
        try:
            async with get_session(create_session_factory(engine)) as session:
                rows = (
                    await session.execute(select(Repository.id, Repository.local_path))
                ).all()
                return [(r[0], r[1]) for r in rows]
        finally:
            await engine.dispose()

    rows = asyncio.run(_read_rows())
    assert len(rows) == 1
    repo_id, local_path = rows[0]
    assert Path(local_path) == moved.resolve()

    async def _load_repo() -> Repository:
        engine = create_engine(resolve_db_url(moved))
        try:
            async with get_session(create_session_factory(engine)) as session:
                row = await session.get(Repository, repo_id)
                assert row is not None
                return row
        finally:
            await engine.dispose()

    repo = asyncio.run(_load_repo())
    assert Path(served_root(repo) or "") == moved.resolve()
